from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Never, TypeVar, assert_never

from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import (
    Aborted,
    Commit,
    Committed,
    Rollback,
    RolledBack,
    TransactionDecision,
    TransactionResult,
    execute_in_uow_scope,
    require_committed,
)
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.escalation import RetryAction
from waku.messaging._internal.polling_agent import (
    DEFAULT_DURABILITY_POLLING_CONFIG,
    AdaptivePace,
    Placement,
    PollingAgent,
)
from waku.messaging.durability import IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.exceptions import DurabilityOwnershipLostError
from waku.messaging.sending.evaluator import SendingFailureContext, SendingFailureEvaluator
from waku.messaging.sending.policy import SendingFailurePolicy
from waku.messaging.transport import MalformedMetadataError
from waku.messaging.transport._internal.registry import TransportRegistry, split_destination
from waku.messaging.transport._internal.wire import wire_metadata_from_entry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from dishka import AsyncContainer

    from waku._internal.node import NodeId
    from waku._internal.polling import PollingConfig
    from waku.messaging._internal.escalation import PolicyOutcome
    from waku.messaging.outbox.models import OutboxMessage

__all__ = [
    'OutboxRelay',
    'OutboxRelayConfig',
]

logger = logging.getLogger(__name__)

_OperationT = TypeVar('_OperationT')

_DEFAULT_RECOVERY_INTERVAL: Final[timedelta] = timedelta(minutes=1)
_DEFAULT_CLEANUP_INTERVAL: Final[timedelta] = timedelta(hours=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxRelayConfig:
    batch_size: int = 100
    polling: PollingConfig = DEFAULT_DURABILITY_POLLING_CONFIG
    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=1)
    max_delay: timedelta = timedelta(seconds=60)
    recovery_interval: timedelta = _DEFAULT_RECOVERY_INTERVAL
    retention: timedelta | None = None
    cleanup_interval: timedelta = _DEFAULT_CLEANUP_INTERVAL
    stop_timeout: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            msg = f'OutboxRelayConfig.batch_size must be >= 1, got {self.batch_size}'
            raise ImproperlyConfiguredError(msg)
        for field_name, value in (
            ('recovery_interval', self.recovery_interval),
            ('cleanup_interval', self.cleanup_interval),
            ('stop_timeout', self.stop_timeout),
        ):
            if value <= timedelta(0):
                msg = f'OutboxRelayConfig.{field_name} must be positive, got {value}'
                raise ImproperlyConfiguredError(msg)


DEFAULT_RELAY_CONFIG: Final = OutboxRelayConfig()


def build_relay_default_policy(config: OutboxRelayConfig) -> SendingFailurePolicy:
    """Build a catch-all sending policy from the relay's retry config.

    Appended at lowest specificity so the relay has ONE retry authority. Retries 1..N-1 with backoff;
    dead-letters at attempt N.
    """
    return (
        SendingFailurePolicy
        .on_any_exception()
        .retry_with_backoff(
            max_attempts=config.max_attempts,
            base_delay=config.base_delay,
            max_delay=config.max_delay,
        )
        .then_move_to_dead_letter()
    )


def _format_error(exc: Exception) -> str:
    return ''.join(traceback.format_exception(exc))


async def _execute_store_operation(
    container: AsyncContainer,
    operation: Callable[[IOutboxStore], Awaitable[_OperationT]],
) -> TransactionResult[_OperationT, Never]:
    async def execute(scope: AsyncContainer) -> Commit[_OperationT]:
        store = await scope.get(IOutboxStore)
        return Commit(await operation(store))

    return await execute_in_uow_scope(container, execute)


async def _execute_fenced_transition(
    container: AsyncContainer,
    operation: Callable[[IOutboxStore], Awaitable[bool]],
    *,
    owner_id: NodeId,
    message_id: UUID,
) -> Committed[None] | Aborted:
    """Run one owner-fenced transition; a rejected fence rolls back and raises ownership loss.

    Modelling the rejection as a ``Rollback`` carrying the error (not a raise) keeps it distinct from
    ``Aborted``: a caller that maps ``Aborted`` to a degraded outcome must never map a lost race to
    one, because the successor — not this relay — owns the message's terminal evidence.
    """

    async def execute(scope: AsyncContainer) -> TransactionDecision[None, DurabilityOwnershipLostError]:
        store = await scope.get(IOutboxStore)
        if not await operation(store):
            return Rollback(DurabilityOwnershipLostError(owner_id, message_id))
        return Commit(None)

    result = await execute_in_uow_scope(container, execute)
    if isinstance(result, RolledBack):
        raise result.value
    return result


class OutboxRelay(PollingAgent):
    # PER_POD: OutboxRelayLifecycleExtension starts a relay on every pod unconditionally, and
    # `FOR UPDATE SKIP LOCKED` claiming makes N competing dispatchers a throughput feature rather than
    # a correctness hazard.
    placement = Placement.PER_POD

    __slots__ = (
        '_config',
        '_container',
        '_node_id',
        '_now',
        '_sending_evaluator',
    )

    def __init__(
        self,
        *,
        container: AsyncContainer,
        config: OutboxRelayConfig,
        sending_failure_evaluator: SendingFailureEvaluator,
        node_id: NodeId,
        now: Now = utc_now,
    ) -> None:
        self._container = container
        self._config = config
        self._sending_evaluator = sending_failure_evaluator
        self._node_id = node_id
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        # Dispatch-only: outbox recovery-sweep + cleanup moved to DurabilityMaintenanceAgent (D9).
        return await self._process_batch()

    def _fenced(
        self,
        operation: Callable[[IOutboxStore], Awaitable[bool]],
        message: OutboxMessage,
    ) -> Awaitable[Committed[None] | Aborted]:
        return _execute_fenced_transition(
            self._container,
            operation,
            owner_id=self._node_id,
            message_id=message.id,
        )

    async def _process_batch(self) -> int:
        async def fetch(store: IOutboxStore) -> Sequence[OutboxMessage]:
            return await store.fetch_head_of_queue(self._config.batch_size, self._node_id)

        messages = require_committed(await _execute_store_operation(self._container, fetch))
        processed = 0
        for message in messages:
            try:
                if await self._dispatch_message(message):
                    processed += 1
            except DurabilityOwnershipLostError as exc:
                # Recovery handed this row to a live relay while the dispatch was mid-flight — this node
                # had left the registry. Nothing was written and the successor owns the outcome, so the
                # row is abandoned here without any terminal record.
                logger.warning('Abandoning outbox message %s: %s', message.id, exc)
        return processed

    async def _dispatch_message(self, message: OutboxMessage) -> bool:
        try:
            metadata = wire_metadata_from_entry(message)
        except MalformedMetadataError as exc:
            # A corrupt metadata blob is deterministic poison, not a transient broker failure: dead-letter
            # immediately (the broker is never touched, no sending-retry budget is burned on a row that can
            # never be rebuilt) — distinct from _on_dispatch_failure's send-side retry policy.
            return await self._handle_exhausted(message, exc)
        if metadata.expires_at is not None and metadata.expires_at <= self._now():
            # The delivery deadline (deliver_by/deliver_within) elapsed while the row sat queued (relay
            # downtime, retries, backpressure). Terminal-DISCARDED (never DLQ'd) before any broker send —
            # the send-side analog of the executor's receive-time discard.
            async def discard_expired(store: IOutboxStore) -> bool:
                return await store.mark_discarded(
                    message.id,
                    'expired before dispatch (delivery deadline elapsed)',
                    owner_id=self._node_id,
                )

            require_committed(await self._fenced(discard_expired, message))
            logger.info(
                'Discarding expired outbox message %s (expires_at=%s) before send', message.id, metadata.expires_at
            )
            return True
        try:
            registry = await self._container.get(TransportRegistry)
            sender = registry.sender_for(message.destination)
            queue = split_destination(message.destination, default_scheme=registry.default_scheme)[1]
            # Resolve with the full URI (not the split queue) — the override map is keyed by the configured
            # BrokerEndpointEntry.send.mapper source URI.
            mapper = registry.mapper_for(message.destination)
            # Broker phase: no database transaction is open. A raise here means NOT delivered, so the
            # sending-failure policy may choose the next independent database mutation.
            await sender.send(message.payload, destination=queue, metadata=metadata, mapper=mapper)
        except Exception as exc:  # noqa: BLE001
            return await self._on_dispatch_failure(message, exc)
        # Record phase: the message IS delivered; a recording failure must never reach the
        # sending policy (it would record a delivered message DISCARDED/DEAD_LETTERED). Roll back and
        # leave the row PROCESSING owned by this node. Recovery reclaims it only once this node leaves
        # the registry (clean shutdown or eviction) and a live successor re-dispatches it — at-least-once
        # holds across a restart, not on a fixed timer (D1-LIVE deleted the age sweep).

        async def record_delivered(store: IOutboxStore) -> bool:
            return await store.mark_dispatched(message.id, owner_id=self._node_id)

        result = await self._fenced(record_delivered, message)
        if isinstance(result, Committed):
            return True
        if isinstance(result, Aborted):
            logger.error(
                'Outbox message %s was delivered but recording dispatch failed; leaving PROCESSING for '
                'recovery once this node leaves the registry (at-least-once across restart)',
                message.id,
                exc_info=result.error,
            )
            return False
        assert_never(result)

    async def _on_dispatch_failure(self, message: OutboxMessage, exc: Exception) -> bool:
        ctx = SendingFailureContext(
            destination=message.destination,
            exc=exc,
            attempt=message.attempts + 1,
        )
        outcome: PolicyOutcome | None = self._sending_evaluator.evaluate(ctx)
        if outcome is None:
            # Evaluator has no catch-all (misconfigured/empty). Safe default: dead-letter.
            return await self._handle_exhausted(message, exc)
        return await self._apply_outcome(message, exc, outcome)

    async def _apply_outcome(
        self,
        message: OutboxMessage,
        exc: Exception,
        outcome: PolicyOutcome,
    ) -> bool:
        match outcome.action:
            case RetryAction.RETRY:
                return await self._reschedule(message, exc, next_retry_at=self._now())
            case RetryAction.RETRY_WITH_BACKOFF:
                delay = outcome.retry_delay or timedelta(0)
                next_retry_at = self._now() + delay
                return await self._reschedule(message, exc, next_retry_at=next_retry_at)
            case RetryAction.DISCARD:

                async def discard(store: IOutboxStore) -> bool:
                    return await store.mark_discarded(message.id, _format_error(exc), owner_id=self._node_id)

                require_committed(await self._fenced(discard, message))
                logger.info('Discarded outbox message %s after %d attempt(s)', message.id, message.attempts + 1)
                return False
            case RetryAction.DEAD_LETTER:
                return await self._handle_exhausted(message, exc)
            case RetryAction.REQUEUE | RetryAction.PAUSE:  # pragma: no cover -- sending policies can't seed these
                msg = f'sending relay received handler-only action {outcome.action.value}'
                raise RuntimeError(msg)
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    async def _reschedule(
        self,
        message: OutboxMessage,
        exc: Exception,
        *,
        next_retry_at: datetime,
    ) -> bool:
        async def reschedule(store: IOutboxStore) -> bool:
            return await store.mark_failed(message.id, _format_error(exc), next_retry_at, owner_id=self._node_id)

        require_committed(await self._fenced(reschedule, message))
        return False

    async def _handle_exhausted(
        self,
        message: OutboxMessage,
        exc: Exception,
    ) -> bool:
        entry = DeadLetterEntry.from_failure(
            message_type=message.message_type,
            payload=message.payload,
            destination=message.destination,
            destination_kind=DeadLetterDestinationKind.ENDPOINT,
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            exc=exc,
            attempt=message.attempts + 1,
            message_id=message.message_id,
            metadata=message.metadata,
            group_id=message.group_id,
        )

        async def move_to_dead_letter(store: IOutboxStore) -> bool:
            return await store.move_to_dead_letter(message.id, entry, owner_id=self._node_id)

        primary = await self._fenced(move_to_dead_letter, message)
        if isinstance(primary, Committed):
            logger.info('Message %s moved to dead letter after %d attempts', message.id, message.attempts + 1)
            return True
        if not isinstance(primary, Aborted):
            assert_never(primary)

        logger.error('Failed to move message %s to dead letter', message.id, exc_info=primary.error)
        error = _format_error(exc)

        async def mark_failed(store: IOutboxStore) -> bool:
            return await store.mark_failed(message.id, error, next_retry_at=None, owner_id=self._node_id)

        fallback = await self._fenced(mark_failed, message)
        if isinstance(fallback, Committed):
            logger.warning('Message %s exhausted after %d attempts', message.id, message.attempts + 1)
            return False
        if isinstance(fallback, Aborted):
            logger.error('Failed to mark message %s as failed', message.id, exc_info=fallback.error)
            raise fallback.error
        assert_never(fallback)
