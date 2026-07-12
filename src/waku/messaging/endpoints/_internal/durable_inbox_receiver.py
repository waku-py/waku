from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio

from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.circuit_breaker import CircuitBreaker, PassthroughCircuitBreaker
from waku.messaging._internal.identifiers import EndpointUri
from waku.messaging._internal.partition import resolve_and_allocate
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
from waku.messaging.endpoints.executor import DEFERRED_TERMINAL_OUTCOMES
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.exceptions import RequeueBudgetExceededError
from waku.messaging.inbox._internal.finalize import apply_inbox_outcome
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta
    from uuid import UUID

    from dishka import AsyncContainer

    from waku.messaging._internal.circuit_breaker import ICircuitBreaker
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.endpoints.outcome import ExecutionOutcome
    from waku.messaging.partition import PartitionKeyExtractor

logger = logging.getLogger(__name__)

__all__ = [
    'DurableInboxReceiver',
    'build_durable_inbox_receiver',
]

# Envelope + the subset of handlers whose inbox row was newly stored (dedup-skipped at persist time).
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


class DurableInboxReceiver:
    """Shared persist-and-process engine for durable inbox endpoints (DurableLocalQueueEndpoint, InboundListener)."""

    __slots__ = (
        '_circuit_breaker',
        '_container',
        '_executor',
        '_inbox_owner_id',
        '_keep_after_handled',
        '_max_requeue_attempts',
        '_partition_by',
        '_requeue_counts',
        '_timed_pauser',
        '_uri',
        '_worker',
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        uri: str,
        container: AsyncContainer,
        executor: EndpointExecutor,
        inbox_owner_id: str,
        keep_after_handled: timedelta,
        partition_by: PartitionKeyExtractor | None = None,
        max_requeue_attempts: int = 5,
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        max_buffer_size: float = math.inf,
        stop_timeout: float = 5.0,
    ) -> None:
        self._uri = uri
        self._container = container
        self._executor = executor
        self._inbox_owner_id = inbox_owner_id
        self._keep_after_handled = keep_after_handled
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
        self._circuit_breaker: ICircuitBreaker = (
            CircuitBreaker(config=circuit_breaker_config, pause=self.pause, resume=self.resume)
            if circuit_breaker_config is not None
            else PassthroughCircuitBreaker()
        )

    @property
    def is_running(self) -> bool:
        return self._worker.is_running

    @property
    def queue_depth(self) -> int:
        # Broker-agnostic meter: the in-memory backlog the listener watermark observes.
        return self._worker.queue_depth

    def attach_circuit_breaker(self, circuit_breaker: CircuitBreaker) -> None:
        # Set before start(): _process_work_item feeds its execute outcomes to the breaker; stop() aclose()s it once.
        self._circuit_breaker = circuit_breaker

    async def persist(
        self,
        envelope: MessageEnvelope[Any],
        handler_types: frozenset[HandlerType],
    ) -> frozenset[HandlerType]:
        """Write one inbox row per handler, deduplicating by (message_id, handler_FQN).

        Returns the subset of handler_types whose row was freshly stored (not already present).
        """
        async with unit_of_work_scope(self._container) as write_scope:
            inbox = await write_scope.get(IInboxStore)
            codec = await write_scope.get(PayloadCodec)
            # Allocate ONCE per message: all per-handler rows share the same position in the partition.
            group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, write_scope)
            payload = encode_payload(envelope, codec)
            metadata = encode_metadata(envelope)
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
                    correlation_id=envelope.correlation_id,
                    causation_id=envelope.causation_id,
                    metadata=metadata,
                )
                if await inbox.store_incoming(entry):
                    fresh.add(handler_type)
        return frozenset(fresh)

    async def enqueue(self, envelope: MessageEnvelope[Any], fresh: frozenset[HandlerType]) -> None:
        await self._worker.send((envelope, frozenset(fresh)))

    async def start(self, *, on_drain: Callable[[int], Awaitable[None]] | None = None) -> None:
        await self._worker.start(self._process_work_item, on_drain=on_drain)

    async def stop(self) -> None:
        await self._timed_pauser.aclose()  # cancel parked auto-resume before the worker force-resumes
        await self._worker.stop()
        await self._circuit_breaker.aclose()

    async def pause(self) -> PauseToken:
        return await self._worker.pause()

    async def resume(self, token: PauseToken | None = None) -> None:
        if token is not None:
            await self._worker.resume(token)

    async def _process_work_item(self, work_item: _WorkItem) -> None:
        envelope, handler_types = work_item
        on_result = self._circuit_breaker.record
        for handler_type in handler_types:
            destination = handler_destination(handler_type)
            result = await self._executor.execute(envelope, handler_type, on_result=on_result)
            if result.outcome in DEFERRED_TERMINAL_OUTCOMES:
                await self._enact_redelivery(
                    envelope,
                    destination,
                    frozenset({handler_type}),
                    result.pause_duration,
                    result.requeue_limit,
                )
            else:
                self._requeue_counts.pop((envelope.message_id, destination), None)
                await self._finalize(envelope, destination, result.outcome)

    async def _enact_redelivery(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_types: frozenset[HandlerType],
        pause_duration: timedelta | None,
        requeue_limit: int | None,
    ) -> None:
        limit = requeue_limit if requeue_limit is not None else self._max_requeue_attempts
        key = (envelope.message_id, destination)
        count = self._requeue_counts.get(key, 0) + 1
        await self._record_requeue_attempt(envelope, destination)
        if count >= limit:
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
            codec = await scope.get(PayloadCodec)
            dead_letter = DeadLetterEntry.from_failure(
                message_type=envelope.message_type,
                payload=encode_payload(envelope, codec),
                destination=destination,
                destination_kind=DeadLetterDestinationKind.HANDLER,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                exc=RequeueBudgetExceededError(envelope.message_id, attempts),
                attempt=attempts,
                message_id=envelope.message_id,
                metadata=encode_metadata(envelope),
                group_id=envelope.group_id,
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


def build_durable_inbox_receiver(  # noqa: PLR0913
    *,
    uri: str,
    container: AsyncContainer,
    executor: EndpointExecutor,
    inbox_owner_id: str,
    keep_after_handled: timedelta,
    partition_by: PartitionKeyExtractor | None = None,
    max_requeue_attempts: int = 5,
    circuit_breaker_config: CircuitBreakerConfig | None = None,
    pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    max_buffer_size: float = math.inf,
    stop_timeout: float = 5.0,
) -> DurableInboxReceiver:
    return DurableInboxReceiver(
        uri=uri,
        container=container,
        executor=executor,
        inbox_owner_id=inbox_owner_id,
        keep_after_handled=keep_after_handled,
        partition_by=partition_by,
        max_requeue_attempts=max_requeue_attempts,
        pause_sleep=pause_sleep,
        circuit_breaker_config=circuit_breaker_config,
        max_buffer_size=max_buffer_size,
        stop_timeout=stop_timeout,
    )
