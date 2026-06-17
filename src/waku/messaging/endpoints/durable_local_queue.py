from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from anyio import create_memory_object_stream
from typing_extensions import override

from waku.di import unit_of_work_scope
from waku.messaging.circuit_breaker.breaker import CircuitBreaker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.inbox._destination import handler_destination
from waku.messaging.inbox.finalize import apply_inbox_outcome
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.partition import resolve_and_allocate
from waku.messaging.transport.serialization import IEnvelopeSerializer

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
    from dishka import AsyncContainer

    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.router import HandlerSubscriptions

logger = logging.getLogger(__name__)

__all__ = [
    'DurableLocalQueueEndpoint',
]

# Work item enqueued onto the memory stream: the envelope plus the subset of subscribed handlers
# whose inbox row was newly stored in dispatch(). Only these still need processing — duplicates
# were filtered at persist time, so the worker never re-checks dedup.
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


class DurableLocalQueueEndpoint(Endpoint):
    """Inbox-backed local queue with per-handler dedup.

    dispatch() persists one inbox row per subscribed handler (keyed by the handler FQN destination)
    and commits BEFORE enqueueing — preserving the persist-before-enqueue durability guarantee for
    the whole fan-out. It then enqueues only the handlers whose row was newly stored (the per-handler
    dedup-skip), so each handler dedups independently. The worker loop drains the stream, executes
    those handlers via EndpointExecutor, and marks each ``(message_id, destination)`` row handled (or
    deletes it on DLQ/discard). Crash recovery is supplied by InboxRecoveryWorker.

    Unlike ``LocalQueueEndpoint`` (which composes ``MemoryStreamWorker``), this owns a single
    sequential worker loop over a ``_WorkItem``-typed stream and does NOT yet support ``max_parallel``
    (bounded-pool parallelism is a future addition). ``pause()``/``resume()`` gate the worker loop
    (the circuit breaker uses them); the inbox claim model (FOR UPDATE SKIP LOCKED, per-handler rows)
    is the cross-pod concurrency mechanism here.
    """

    __slots__ = (
        '_circuit_breaker',
        '_container',
        '_executor',
        '_handler_subscriptions',
        '_inbox_owner_id',
        '_keep_after_handled',
        '_max_buffer_size',
        '_partition_by',
        '_paused',
        '_receive_stream',
        '_send_stream',
        '_stop_timeout',
        '_worker_task',
    )

    def __init__(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: EndpointExecutor,
        container: AsyncContainer,
        inbox_config_keep_after_handled_seconds: float,
        inbox_owner_id: str,
        stop_timeout: float,
        max_buffer_size: float,
        partition_by: PartitionKeyExtractor | None = None,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._container = container
        self._inbox_owner_id = inbox_owner_id
        self._keep_after_handled = timedelta(seconds=inbox_config_keep_after_handled_seconds)
        self._stop_timeout = stop_timeout
        self._max_buffer_size = max_buffer_size
        self._partition_by = partition_by
        self._send_stream: MemoryObjectSendStream[_WorkItem] | None = None
        self._receive_stream: MemoryObjectReceiveStream[_WorkItem] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._paused = asyncio.Event()
        self._paused.set()  # not paused by default
        self._circuit_breaker: CircuitBreaker | None = (
            CircuitBreaker(config=circuit_breaker_config, pause=self.pause, resume=self.resume)
            if circuit_breaker_config is not None
            else None
        )

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Persist one inbox row per subscribed handler in OWN scope, then enqueue.

        Persist-before-enqueue across the whole fan-out: every subscribed handler's
        ``(message_id, destination=handler_FQN)`` row is written and committed BEFORE anything reaches
        the memory stream, so a crash after commit leaves durable INCOMING rows that InboxRecoveryWorker
        reclaims. Only handlers whose row was newly stored are enqueued — the per-handler dedup-skip.

        The ``scope`` parameter is part of the ``Endpoint`` ABC signature but is NOT used here on
        purpose: committing the caller's UoW would prematurely commit a handler's business transaction
        when this endpoint is used from a cascading send. A dedicated scope is opened for the inbox write,
        matching ``OutboxRelay`` and ``DurableReceiver``.
        """
        send_stream = self._send_stream
        if send_stream is None:
            logger.warning('Message dropped: endpoint %s is stopped (message_id=%s)', self._uri, envelope.message_id)
            return

        handler_types = self._handler_subscriptions.get(type(envelope.payload), frozenset())
        if not handler_types:
            return

        async with unit_of_work_scope(self._container) as write_scope:
            inbox = await write_scope.get(IInboxStore)
            serializer = await write_scope.get(IEnvelopeSerializer)
            # Allocate ONCE per message: every per-handler row shares the message's position in the
            # partition. Per-handler ordering is then by (group_id, destination) head-of-queue.
            group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, write_scope)
            payload = serializer.serialize(envelope)
            fresh: set[HandlerType] = set()
            for handler_type in handler_types:
                entry = InboxEntry(
                    id=envelope.message_id,
                    payload=payload,
                    message_type=envelope.message_type,
                    source_uri=self._uri,
                    destination=handler_destination(handler_type),
                    group_id=group_id,
                    sequence_number=sequence_number,
                    owner_id=self._inbox_owner_id,
                )
                if await inbox.store_incoming(entry):
                    fresh.add(handler_type)

        if not fresh:
            logger.debug('Duplicate message discarded for all handlers: message_id=%s', envelope.message_id)
            return

        await send_stream.send((envelope, frozenset(fresh)))

    @override
    async def start(self) -> None:
        send, receive = create_memory_object_stream[_WorkItem](max_buffer_size=self._max_buffer_size)
        self._send_stream = send
        self._receive_stream = receive
        self._worker_task = asyncio.create_task(self._worker_loop(receive))

    @override
    async def stop(self) -> None:
        self._paused.set()  # unblock a paused worker so it can observe the closed stream
        send_stream, self._send_stream = self._send_stream, None
        if send_stream is not None:
            send_stream.close()
        if self._worker_task is not None:
            await self._drain_worker(self._worker_task)
            self._worker_task = None
        if self._receive_stream is not None:
            self._receive_stream.close()
            self._receive_stream = None
        if self._circuit_breaker is not None:
            await self._circuit_breaker.aclose()

    @override
    async def pause(self) -> None:
        self._paused.clear()

    @override
    async def resume(self) -> None:
        self._paused.set()

    async def _drain_worker(self, task: asyncio.Task[None]) -> None:
        try:
            with anyio.fail_after(self._stop_timeout):
                await task
        except TimeoutError:
            logger.warning(
                'Worker task for %s did not terminate within %.1fs, cancelling',
                self._uri,
                self._stop_timeout,
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _worker_loop(self, receive_stream: MemoryObjectReceiveStream[_WorkItem]) -> None:
        async for envelope, handler_types in receive_stream:
            await self._paused.wait()
            try:
                await self._process_envelope(envelope, handler_types)
            except Exception:
                logger.exception(
                    'Unhandled error processing message_id=%s, continuing worker loop',
                    envelope.message_id,
                )

    async def _process_envelope(self, envelope: MessageEnvelope[Any], handler_types: frozenset[HandlerType]) -> None:
        # Process only the handlers whose row was newly stored in dispatch(). Dedup was already
        # resolved at persist time, so no re-check here.
        on_result = self._circuit_breaker.record if self._circuit_breaker is not None else None
        for handler_type in handler_types:
            outcome = await self._executor.execute(envelope, handler_type, on_result=on_result)
            await self._finalize(envelope, handler_destination(handler_type), outcome)

    async def _finalize(self, envelope: MessageEnvelope[Any], destination: str, outcome: ExecutionOutcome) -> None:
        await apply_inbox_outcome(
            self._container,
            entry_id=envelope.message_id,
            destination=destination,
            outcome=outcome,
            keep_after_handled=self._keep_after_handled,
        )
