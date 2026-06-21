from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import unit_of_work_scope
from waku.messaging._identifiers import EndpointUri
from waku.messaging.circuit_breaker.breaker import CircuitBreaker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.endpoints.executor import DEFERRED_TERMINAL_OUTCOMES
from waku.messaging.endpoints.worker import MemoryStreamWorker
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.exceptions import RequeueBudgetExceededError
from waku.messaging.inbox._destination import handler_destination
from waku.messaging.inbox.finalize import apply_inbox_outcome
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.partition import resolve_and_allocate, resolve_group_id
from waku.messaging.transport.serialization import IEnvelopeSerializer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from uuid import UUID

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.pauser import PauseToken
    from waku.messaging.router import HandlerSubscriptions

logger = logging.getLogger(__name__)

__all__ = [
    'DurableLocalQueueEndpoint',
]

# Envelope + the subset of handlers whose inbox row was newly stored (dedup-skipped at persist time).
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


class DurableLocalQueueEndpoint(Endpoint):
    """Inbox-backed local queue with per-handler ``(message_id, handler_FQN)`` dedup.

    Commits inbox rows BEFORE enqueueing — a crash after commit leaves INCOMING rows for recovery.
    Only freshly-stored handlers are enqueued; the worker finalizes each via ``EndpointExecutor``.
    Cross-pod concurrency is via FOR UPDATE SKIP LOCKED on inbox rows. Stream/task/pause lifecycle is
    delegated to a single-consumer ``MemoryStreamWorker`` (composition, as ``LocalQueueEndpoint``).
    """

    __slots__ = (
        '_circuit_breaker',
        '_container',
        '_executor',
        '_handler_subscriptions',
        '_inbox_owner_id',
        '_keep_after_handled',
        '_max_requeue_attempts',
        '_now',
        '_partition_by',
        '_requeue_counts',
        '_timed_pauser',
        '_worker',
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
        max_requeue_attempts: int = 5,
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        now: Now = utc_now,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._container = container
        self._now = now
        self._inbox_owner_id = inbox_owner_id
        self._keep_after_handled = timedelta(seconds=inbox_config_keep_after_handled_seconds)
        self._partition_by = partition_by
        self._max_requeue_attempts = max_requeue_attempts
        # per-(message, handler) requeue counter; mirrored to the durable row by increment_attempts.
        self._requeue_counts: dict[tuple[UUID, str], int] = {}
        # Single sequential consumer (max_parallel=1); durable ordering relies on it.
        self._worker: MemoryStreamWorker[_WorkItem] = MemoryStreamWorker(
            max_buffer_size=max_buffer_size,
            stop_timeout=stop_timeout,
            max_parallel=1,
        )
        self._timed_pauser = self._worker.make_timed_pauser(sleep=pause_sleep)
        self._circuit_breaker: CircuitBreaker | None = (
            CircuitBreaker(config=circuit_breaker_config, pause=self.pause, resume=self.resume)
            if circuit_breaker_config is not None
            else None
        )

    @property
    @override
    def supports_scheduling(self) -> bool:
        return True  # durable inbox survives restarts; promotion runs the message when due

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Persist one inbox row per handler in a dedicated scope (not the caller's), then enqueue.

        Caller's ``scope`` is unused: reusing it would commit a business transaction prematurely in cascading-send.
        """
        if not self._worker.is_running:
            logger.warning('Message dropped: endpoint %s is stopped (message_id=%s)', self._uri, envelope.message_id)
            return

        handler_types = self._handler_subscriptions.get(type(envelope.payload), frozenset())
        if not handler_types:
            return

        scheduled = envelope.scheduled_time
        if scheduled is not None and scheduled > self._now():
            # SCHEDULED rows: no sequence allocated, no enqueue — memory stream can't survive a restart.
            # Promotion allocates the sequence at due-time so delayed messages sort after already-queued siblings (BLOCKER 1).
            await self._store_scheduled(envelope, handler_types, scheduled)
            return

        async with unit_of_work_scope(self._container) as write_scope:
            inbox = await write_scope.get(IInboxStore)
            serializer = await write_scope.get(IEnvelopeSerializer)
            # Allocate ONCE per message: all per-handler rows share the same position in the partition.
            group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, write_scope)
            payload = serializer.serialize(envelope)
            fresh: set[HandlerType] = set()
            for handler_type in handler_types:
                entry = InboxEntry(
                    id=envelope.message_id,
                    payload=payload,
                    message_type=envelope.message_type,
                    source_uri=EndpointUri(self._uri),
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

        await self._worker.send((envelope, frozenset(fresh)))

    async def _store_scheduled(
        self,
        envelope: MessageEnvelope[Any],
        handler_types: frozenset[HandlerType],
        scheduled: datetime,
    ) -> None:
        group_id = resolve_group_id(envelope, self._partition_by)  # partition resolved; sequence deferred
        async with unit_of_work_scope(self._container) as write_scope:
            inbox = await write_scope.get(IInboxStore)
            serializer = await write_scope.get(IEnvelopeSerializer)
            payload = serializer.serialize(envelope)
            for handler_type in handler_types:
                # owner_id=None so the recovery drain (owner_id IS NULL) claims the promoted row.
                await inbox.store_incoming(
                    InboxEntry(
                        id=envelope.message_id,
                        payload=payload,
                        message_type=envelope.message_type,
                        source_uri=EndpointUri(self._uri),
                        destination=handler_destination(handler_type),
                        group_id=group_id,
                        sequence_number=None,
                        owner_id=None,
                        status=InboxStatus.SCHEDULED,
                        execution_time=scheduled,
                    )
                )

    @override
    async def start(self) -> None:
        await self._worker.start(self._process_work_item)

    @override
    async def stop(self) -> None:
        await self._timed_pauser.aclose()  # cancel parked auto-resume before the worker force-resumes
        await self._worker.stop()
        if self._circuit_breaker is not None:
            await self._circuit_breaker.aclose()

    @override
    async def pause(self) -> PauseToken:
        return await self._worker.pause()

    @override
    async def resume(self, token: PauseToken | None = None) -> None:
        if token is not None:
            await self._worker.resume(token)

    async def _process_work_item(self, work_item: _WorkItem) -> None:
        envelope, handler_types = work_item
        on_result = self._circuit_breaker.record if self._circuit_breaker is not None else None
        for handler_type in handler_types:
            destination = handler_destination(handler_type)
            result = await self._executor.execute(envelope, handler_type, on_result=on_result)
            if result.outcome in DEFERRED_TERMINAL_OUTCOMES:
                await self._enact_redelivery(envelope, destination, frozenset({handler_type}), result.pause_duration)
            else:
                self._requeue_counts.pop((envelope.message_id, destination), None)
                await self._finalize(envelope, destination, result.outcome)

    async def _enact_redelivery(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_types: frozenset[HandlerType],
        pause_duration: timedelta | None,
    ) -> None:
        key = (envelope.message_id, destination)
        count = self._requeue_counts.get(key, 0) + 1
        await self._record_requeue_attempt(envelope, destination)
        if count >= self._max_requeue_attempts:
            self._requeue_counts.pop(key, None)
            await self._dead_letter_poison(envelope, destination, count)
            return  # budget exhausted → DLQ
        if not self._worker.is_running:
            self._requeue_counts.pop(key, None)  # stopped; INCOMING row survives for recovery
            return
        if not self._worker.try_send((envelope, handler_types)):
            self._requeue_counts.pop(key, None)
            await self._dead_letter_poison(envelope, destination, count)  # full buffer → DLQ, never block
            return
        self._requeue_counts[key] = count
        if pause_duration is not None:
            await self._timed_pauser.pause(pause_duration)

    async def _record_requeue_attempt(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        async with unit_of_work_scope(self._container) as scope:
            inbox = await scope.get(IInboxStore)
            await inbox.increment_attempts(envelope.message_id, destination)

    async def _dead_letter_poison(self, envelope: MessageEnvelope[Any], destination: str, attempts: int) -> None:
        async with unit_of_work_scope(self._container) as scope:
            inbox = await scope.get(IInboxStore)
            serializer = await scope.get(IEnvelopeSerializer)
            dead_letter = DeadLetterEntry.from_failure(
                message_type=envelope.message_type,
                payload=serializer.serialize(envelope),
                destination=destination,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                exc=RequeueBudgetExceededError(envelope.message_id, attempts),
                attempt=attempts,
            )
            await inbox.move_to_dead_letter(envelope.message_id, destination, dead_letter)

    async def _finalize(self, envelope: MessageEnvelope[Any], destination: str, outcome: ExecutionOutcome) -> None:
        await apply_inbox_outcome(
            self._container,
            entry_id=envelope.message_id,
            destination=destination,
            outcome=outcome,
            keep_after_handled=self._keep_after_handled,
        )
