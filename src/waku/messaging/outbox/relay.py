from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, assert_never

from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.polling import PollingConfig
from waku.messaging._internal.escalation import RetryAction
from waku.messaging._internal.polling_agent import AdaptivePace, Placement, PollingAgent
from waku.messaging.durability import IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.sending.evaluator import SendingFailureContext, SendingFailureEvaluator
from waku.messaging.sending.policy import SendingFailurePolicy
from waku.messaging.transport._internal.registry import TransportRegistry, split_destination
from waku.messaging.transport._internal.wire import wire_metadata_from_entry
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging._internal.escalation import PolicyOutcome
    from waku.messaging.outbox.models import OutboxMessage

__all__ = [
    'OutboxRelay',
    'OutboxRelayConfig',
]

logger = logging.getLogger(__name__)

_DEFAULT_STUCK_THRESHOLD = timedelta(minutes=5)
_DEFAULT_RECOVERY_INTERVAL = timedelta(minutes=1)
_DEFAULT_CLEANUP_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxRelayConfig:
    batch_size: int = 100
    polling: PollingConfig = PollingConfig(  # noqa: RUF009
        poll_interval_min_seconds=1.0,
        poll_interval_max_seconds=30.0,
        poll_interval_step_seconds=1.0,
        poll_interval_jitter_factor=0.1,
    )
    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=1)
    max_delay: timedelta = timedelta(seconds=60)
    stuck_threshold: timedelta = _DEFAULT_STUCK_THRESHOLD
    recovery_interval: timedelta = _DEFAULT_RECOVERY_INTERVAL
    retention: timedelta | None = None
    cleanup_interval: timedelta = _DEFAULT_CLEANUP_INTERVAL
    stop_timeout: timedelta = timedelta(seconds=10)


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


class OutboxRelay(PollingAgent):
    placement = Placement.SINGLETON_PER_DC

    __slots__ = (
        '_config',
        '_container',
        '_now',
        '_sending_evaluator',
    )

    def __init__(
        self,
        *,
        container: AsyncContainer,
        config: OutboxRelayConfig,
        sending_failure_evaluator: SendingFailureEvaluator,
        now: Now = utc_now,
    ) -> None:
        self._container = container
        self._config = config
        self._sending_evaluator = sending_failure_evaluator
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        # Dispatch-only: outbox recovery-sweep + cleanup moved to DurabilityMaintenanceAgent (D9).
        return await self._process_batch()

    async def _process_batch(self) -> int:
        async with self._container() as batch_scope:
            store = await batch_scope.get(IOutboxStore)
            uow = await batch_scope.get(IUnitOfWork)
            messages = await store.fetch_head_of_queue(self._config.batch_size)
            await uow.commit()
        processed = 0
        for message in messages:
            async with self._container() as scope:
                try:
                    await self._dispatch_message(scope, message)
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    await self._on_dispatch_failure(scope, message, exc)
        return processed

    async def _dispatch_message(self, scope: AsyncContainer, message: OutboxMessage) -> None:
        store = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)
        metadata = wire_metadata_from_entry(message)
        if metadata.expires_at is not None and metadata.expires_at <= self._now():
            # The delivery deadline (deliver_by/deliver_within) elapsed while the row sat queued (relay
            # downtime, retries, backpressure). Terminal-DISCARDED (never DLQ'd) before any broker send —
            # the send-side analog of the executor's receive-time discard.
            await store.mark_discarded(message.id, 'expired before dispatch (delivery deadline elapsed)')
            await uow.commit()
            logger.info(
                'Discarding expired outbox message %s (expires_at=%s) before send', message.id, metadata.expires_at
            )
            return
        registry = await scope.get(TransportRegistry)
        sender = registry.sender_for(message.destination)
        queue = split_destination(message.destination, default_scheme=registry.default_scheme)[1]
        # Resolve with the full URI (not the split queue) — the override map is keyed by the configured
        # BrokerEndpointEntry.send.mapper source URI.
        mapper = registry.mapper_for(message.destination)
        # Phase 1 — send: a raise here means NOT delivered -> propagate to the caller's except,
        # which runs the sending-failure policy (_on_dispatch_failure).
        await sender.send(message.payload, destination=queue, metadata=metadata, mapper=mapper)
        # Phase 2 — record: the message IS delivered; a recording failure must never reach the
        # sending policy (it would record a delivered message DISCARDED/DEAD_LETTERED). Roll back
        # and leave the row PROCESSING so recover_stuck re-dispatches it (at-least-once).
        try:
            await store.mark_dispatched(message.id)
            await uow.commit()
        except Exception:
            await uow.rollback()
            logger.exception(
                'Outbox message %s was delivered but recording dispatch failed; '
                'leaving PROCESSING for recovery (at-least-once)',
                message.id,
            )

    async def _on_dispatch_failure(self, scope: AsyncContainer, message: OutboxMessage, exc: Exception) -> None:
        store = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)
        await uow.rollback()

        ctx = SendingFailureContext(
            destination=message.destination,
            exc=exc,
            attempt=message.retry_count + 1,
        )
        outcome: PolicyOutcome | None = self._sending_evaluator.evaluate(ctx)
        if outcome is None:
            # Evaluator has no catch-all (misconfigured/empty). Safe default: dead-letter.
            await self._handle_exhausted(store, uow, message, exc)
            return
        await self._apply_outcome(store, uow, message, exc, outcome)

    async def _apply_outcome(
        self,
        store: IOutboxStore,
        uow: IUnitOfWork,
        message: OutboxMessage,
        exc: Exception,
        outcome: PolicyOutcome,
    ) -> None:
        match outcome.action:
            case RetryAction.RETRY:
                await self._reschedule(store, uow, message, exc, next_retry_at=datetime.now(tz=UTC))
            case RetryAction.RETRY_WITH_BACKOFF:
                delay = outcome.retry_delay or timedelta(0)
                next_retry_at = datetime.now(tz=UTC) + delay
                await self._reschedule(store, uow, message, exc, next_retry_at=next_retry_at)
            case RetryAction.DISCARD:
                await store.mark_discarded(message.id, _format_error(exc))
                await uow.commit()
                logger.info('Discarded outbox message %s after %d attempt(s)', message.id, message.retry_count + 1)
            case RetryAction.DEAD_LETTER:
                await self._handle_exhausted(store, uow, message, exc)
            case RetryAction.REQUEUE | RetryAction.PAUSE:  # pragma: no cover -- sending policies can't seed these
                msg = f'sending relay received handler-only action {outcome.action.value}'
                raise RuntimeError(msg)
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    @staticmethod
    async def _reschedule(
        store: IOutboxStore,
        uow: IUnitOfWork,
        message: OutboxMessage,
        exc: Exception,
        *,
        next_retry_at: datetime,
    ) -> None:
        await store.mark_failed(message.id, _format_error(exc), next_retry_at)
        await uow.commit()

    @staticmethod
    async def _handle_exhausted(
        store: IOutboxStore,
        uow: IUnitOfWork,
        message: OutboxMessage,
        exc: Exception,
    ) -> None:
        entry = DeadLetterEntry.from_failure(
            message_type=message.message_type,
            payload=message.payload,
            destination=message.destination,
            destination_kind=DeadLetterDestinationKind.ENDPOINT,
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            exc=exc,
            attempt=message.retry_count + 1,
            message_id=message.message_id,
            metadata=message.metadata,
            group_id=message.group_id,
        )
        try:
            await store.move_to_dead_letter(message.id, entry)
            await uow.commit()
        except Exception:
            logger.exception('Failed to move message %s to dead letter', message.id)
            await uow.rollback()
        else:
            logger.info('Message %s moved to dead letter after %d attempts', message.id, message.retry_count + 1)
            return
        error = _format_error(exc)
        try:
            await store.mark_failed(message.id, error, next_retry_at=None)
            await uow.commit()
        except Exception:
            logger.exception('Failed to mark message %s as failed', message.id)
        else:
            logger.warning('Message %s exhausted after %d attempts', message.id, message.retry_count + 1)
