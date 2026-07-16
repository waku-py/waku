from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Never, TypeAlias, TypeVar, assert_never

import anyio

from waku._internal.transaction import (
    Aborted,
    Commit,
    Committed,
    RolledBack,
    TransactionDecision,
    execute_in_uow_scope,
)
from waku.messaging._internal.circuit_breaker import CircuitBreaker, ICircuitBreaker, PassthroughCircuitBreaker
from waku.messaging._internal.partition import resolve_and_allocate
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.execution import ExecutionResult, TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints._internal.redelivery import RedeliveryCoordinator, RedeliveryHooks
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.inbox import EndpointUri
from waku.messaging.inbox._internal.finalize import apply_inbox_outcome
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.execution import IEndpointExecution
    from waku.messaging.endpoints.outcome import ExecutionOutcome
    from waku.messaging.partition import PartitionKeyExtractor

logger = logging.getLogger(__name__)

__all__ = [
    'DurableInboxReceiver',
]

_CommittedT = TypeVar('_CommittedT')

# Envelope + the subset of handlers whose inbox row was newly stored (dedup-skipped at persist time).
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


def _require_committed(result: Committed[_CommittedT] | RolledBack[Never] | Aborted) -> _CommittedT:
    if isinstance(result, Committed):
        return result.value
    if isinstance(result, Aborted):
        raise result.error
    if isinstance(result, RolledBack):
        assert_never(result.value)
    assert_never(result)


class DurableInboxReceiver:
    """Shared persist-and-process engine for durable inbox endpoints (DurableLocalQueueEndpoint, InboundListener)."""

    __slots__ = (
        '_circuit_breaker',
        '_container',
        '_executor',
        '_inbox_owner_id',
        '_keep_after_handled',
        '_partition_by',
        '_redelivery',
        '_timed_pauser',
        '_uri',
        '_worker',
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        uri: str,
        container: AsyncContainer,
        executor: IEndpointExecution,
        inbox_owner_id: str,
        keep_after_handled: timedelta,
        partition_by: PartitionKeyExtractor | None = None,
        max_requeue_attempts: int = 5,
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        max_buffer_size: float = math.inf,
        stop_timeout: timedelta = timedelta(seconds=5),
    ) -> None:
        self._uri = uri
        self._container = container
        self._executor = executor
        self._inbox_owner_id = inbox_owner_id
        self._keep_after_handled = keep_after_handled
        self._partition_by = partition_by
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
        # DURABLE mirrors each attempt onto the inbox row and transitions it on a terminal outcome; a
        # stopped worker keeps the INCOMING row for recovery (the default noop on_stopped, no DLQ).
        self._redelivery = RedeliveryCoordinator(
            worker=self._worker,
            timed_pauser=self._timed_pauser,
            max_requeue_attempts=max_requeue_attempts,
            hooks=RedeliveryHooks(
                dead_letter=self._finalize,
                record_attempt=self._record_requeue_attempt,
                finalize=self._finalize,
            ),
        )

    @property
    def is_running(self) -> bool:
        return self._worker.is_running

    @property
    def queue_depth(self) -> int:
        # Broker-agnostic meter: the in-memory backlog the listener watermark observes.
        return self._worker.queue_depth

    def attach_circuit_breaker(self, circuit_breaker: ICircuitBreaker) -> None:
        # Set before start(): owner-finalized terminal outcomes feed the breaker; stop() aclose()s it once.
        self._circuit_breaker = circuit_breaker

    async def persist(
        self,
        envelope: MessageEnvelope[Any],
        handler_types: frozenset[HandlerType],
    ) -> frozenset[HandlerType]:
        """Write one inbox row per handler, deduplicating by (message_id, handler_FQN).

        Returns the subset of handler_types whose row was freshly stored (not already present).
        """

        async def write(write_scope: AsyncContainer) -> TransactionDecision[frozenset[HandlerType], Never]:
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
            return Commit(frozenset(fresh))

        return _require_committed(await execute_in_uow_scope(self._container, write))

    async def enqueue(self, envelope: MessageEnvelope[Any], fresh: frozenset[HandlerType]) -> None:
        await self._worker.send((envelope, fresh))

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
        for handler_type in handler_types:
            intent = await self._executor.execute(envelope, handler_type)
            evidence = await self._redelivery.handle_intent(envelope, handler_type, intent)
            if evidence is not None:
                terminal_intent, outcome = evidence
                await self._emit_terminal(envelope, handler_type, terminal_intent, outcome)

    async def _record_requeue_attempt(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        destination = handler_destination(handler_type)

        async def increment(scope: AsyncContainer) -> TransactionDecision[None, Never]:
            inbox = await scope.get(IInboxStore)
            await inbox.increment_attempts(envelope.message_id, destination)
            return Commit(None)

        _require_committed(await execute_in_uow_scope(self._container, increment))

    async def _finalize(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
    ) -> ExecutionOutcome:
        destination = handler_destination(handler_type)
        dead_letter = await self._dead_letter_entry(envelope, handler_type, intent)
        result = await apply_inbox_outcome(
            self._container,
            entry_id=envelope.message_id,
            destination=destination,
            intent=intent,
            keep_after_handled=self._keep_after_handled,
            dead_letter=dead_letter,
        )
        return result.outcome

    async def _dead_letter_entry(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
    ) -> DeadLetterEntry | None:
        if intent.kind is not TerminalIntentKind.DEAD_LETTER:
            return None
        if intent.error is None:
            msg = 'dead-letter intent must retain its handler failure'
            raise RuntimeError(msg)
        codec = await self._container.get(PayloadCodec)
        return DeadLetterEntry.from_failure(
            message_type=envelope.message_type,
            payload=encode_payload(envelope, codec),
            destination=handler_destination(handler_type),
            destination_kind=DeadLetterDestinationKind.HANDLER,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            exc=intent.error,
            attempt=intent.attempt,
            message_id=envelope.message_id,
            metadata=encode_metadata(envelope),
            group_id=envelope.group_id,
        )

    async def _emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        outcome: ExecutionOutcome,
    ) -> None:
        result = ExecutionResult(
            outcome,
            pause_duration=intent.pause_duration,
            requeue_limit=intent.requeue_limit,
        )
        await self._executor.emit_terminal(
            envelope,
            handler_type,
            intent,
            result,
        )
        await self._circuit_breaker.record(outcome, intent.error)
