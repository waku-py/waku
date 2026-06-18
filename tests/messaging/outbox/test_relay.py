from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import anyio
from dishka import make_async_container
from typing_extensions import override

from waku.messaging import PollingConfig
from waku.messaging._escalation import RetryAction, walk_stages  # noqa: PLC2701
from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig, build_relay_default_policy
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicy, SendingFailurePolicyRegistry
from waku.messaging.transport.interfaces import ITransport

from tests.messaging.helpers import (
    RecordingTransport,
    RelayDepsProvider,
    make_envelope,
    make_relay_evaluator,
    make_serializer,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    import pytest

    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _TestEvent(IEvent):
    value: str


class _FailingTransport(ITransport):
    @override
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None:
        msg = 'transport down'
        raise ConnectionError(msg)


@dataclass(frozen=True, slots=True)
class _FailureRecord:
    message_id: UUID
    error: str
    next_retry_at: datetime | None


@dataclass
class _TrackingOutboxStore(IOutboxStore):
    pending: list[OutboxMessage] = field(default_factory=list)
    dispatched_ids: list[UUID] = field(default_factory=list)
    dead_lettered_ids: list[UUID] = field(default_factory=list)
    dead_letter_entries: list[DeadLetterEntry] = field(default_factory=list)
    failed_ids: list[UUID] = field(default_factory=list)
    failure_records: list[_FailureRecord] = field(default_factory=list)
    discarded_ids: list[UUID] = field(default_factory=list)
    recovered: int = 0
    move_to_dead_letter_error: Exception | None = None
    mark_failed_error: Exception | None = None

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:  # pragma: no cover
        self.pending.extend(messages)

    @override
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        batch = self.pending[:batch_size]
        self.pending = self.pending[batch_size:]
        return batch

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        # Existing relay tests stage non-partitioned messages, so head-of-queue degrades to FIFO —
        # delegate to keep the in-flight slicing semantics identical.
        return await self.fetch_and_mark_processing(batch_size)

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        self.dispatched_ids.append(message_id)

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        if self.mark_failed_error is not None:
            raise self.mark_failed_error
        self.failed_ids.append(message_id)
        self.failure_records.append(_FailureRecord(message_id=message_id, error=error, next_retry_at=next_retry_at))

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:
        if self.move_to_dead_letter_error is not None:
            raise self.move_to_dead_letter_error
        self.dead_lettered_ids.append(message_id)
        self.dead_letter_entries.append(entry)

    @override
    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        self.discarded_ids.append(message_id)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        self.recovered += 1
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:  # pragma: no cover
        return 0


def _make_outbox_message(envelope: MessageEnvelope[Any]) -> OutboxMessage:
    serializer = make_serializer(_TestEvent)
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=serializer.serialize(envelope),
        destination='test://dest',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
    )


def _make_pending_store() -> tuple[_TrackingOutboxStore, OutboxMessage]:
    store = _TrackingOutboxStore()
    envelope = make_envelope(_TestEvent(value='test'))
    msg = _make_outbox_message(envelope)
    store.pending.append(msg)
    return store, msg


_FAST_POLLING = PollingConfig(
    poll_interval_min_seconds=0.01,
    poll_interval_max_seconds=0.05,
    poll_interval_step_seconds=0.01,
)

_FAST_CONFIG = OutboxRelayConfig(
    polling=_FAST_POLLING,
    recovery_interval=timedelta(hours=1),
)

_EXHAUST_ON_FIRST_FAILURE_CONFIG = OutboxRelayConfig(
    polling=_FAST_POLLING,
    recovery_interval=timedelta(hours=1),
    max_attempts=1,
)


class TestOutboxRelayConfigPolling:
    @staticmethod
    def test_polling_defaults_preserve_relay_cadence() -> None:
        config = OutboxRelayConfig()
        assert config.polling.poll_interval_min_seconds == 1.0
        assert config.polling.poll_interval_max_seconds == 30.0
        assert config.polling.poll_interval_step_seconds == 1.0
        assert config.polling.poll_interval_jitter_factor == 0.1

    @staticmethod
    def test_polling_is_overridable() -> None:
        config = OutboxRelayConfig(polling=PollingConfig(poll_interval_min_seconds=0.25))
        assert config.polling.poll_interval_min_seconds == 0.25
        # A partial override replaces the whole embedded object: un-set fields fall back to
        # PollingConfig's own defaults, not the relay's cadence.
        assert config.polling.poll_interval_max_seconds == 5.0


def test_build_relay_default_policy_mirrors_config() -> None:
    policy = build_relay_default_policy(OutboxRelayConfig(max_attempts=5))
    assert policy.exception_type is None  # on_any_exception catch-all
    actions = [s.action for s in policy.stages]
    assert actions == [RetryAction.RETRY_WITH_BACKOFF, RetryAction.DEAD_LETTER]
    assert policy.stages[0].max_attempts == 5


def test_build_relay_default_policy_boundary_matches_legacy_loop() -> None:
    # Pins behavior-equivalence with the legacy fixed loop for N>1: with relay attempt = retry_count+1,
    # retries at attempts 1..N-1, dead-letters at attempt N. A mutation to walk_stages' boundary
    # (< vs <=) is caught here.
    stages = build_relay_default_policy(OutboxRelayConfig(max_attempts=2)).stages
    assert walk_stages(stages, attempt=1).action is RetryAction.RETRY_WITH_BACKOFF
    assert walk_stages(stages, attempt=2).action is RetryAction.DEAD_LETTER


@asynccontextmanager
async def _run_relay(
    provider: RelayDepsProvider,
    config: OutboxRelayConfig = _FAST_CONFIG,
    *,
    sleep: float = 0.1,
    evaluator: SendingFailureEvaluator | None = None,
) -> AsyncGenerator[None]:
    async with make_async_container(provider) as container:
        relay = OutboxRelay(
            container=container,
            config=config,
            sending_failure_evaluator=evaluator or make_relay_evaluator(config),
        )
        await relay.start()
        await anyio.sleep(sleep)
        try:
            yield
        finally:
            await relay.stop()


class TestOutboxRelay:
    @staticmethod
    async def test_processes_pending_messages() -> None:
        store, msg = _make_pending_store()
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(RelayDepsProvider(store, transport, serializer)):
            pass

        assert msg.id in store.dispatched_ids
        assert len(transport.sent) == 1
        assert transport.sent[0][1] == 'test://dest'

    @staticmethod
    async def test_marks_failed_on_transport_error() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(RelayDepsProvider(store, _FailingTransport(), serializer)):
            pass

        assert msg.id in store.failed_ids

    @staticmethod
    async def test_no_messages_is_noop() -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(RelayDepsProvider(store, transport, serializer), sleep=0.05):
            pass

        assert len(transport.sent) == 0
        assert len(store.dispatched_ids) == 0

    @staticmethod
    async def test_stop_cancels_sleep_immediately() -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        slow_config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=10.0),
            recovery_interval=timedelta(hours=1),
        )

        async with make_async_container(RelayDepsProvider(store, transport, serializer)) as container:
            relay = OutboxRelay(
                container=container,
                config=slow_config,
                sending_failure_evaluator=make_relay_evaluator(slow_config),
            )
            await relay.start()
            await anyio.sleep(0.05)
            await asyncio.wait_for(relay.stop(), timeout=1.0)

    @staticmethod
    async def test_exhausted_message_moved_to_dead_letter() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            pass

        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert len(store.dead_letter_entries) == 1
        entry = store.dead_letter_entries[0]
        assert isinstance(entry, DeadLetterEntry)
        assert entry.destination == 'test://dest'
        assert entry.retry_count == 1

    @staticmethod
    async def test_exhausted_message_falls_back_to_mark_failed_when_move_to_dead_letter_raises() -> None:
        store, msg = _make_pending_store()
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        serializer = make_serializer(_TestEvent)

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            pass

        assert msg.id in store.failed_ids
        assert msg.id not in store.dead_lettered_ids
        assert len(store.failure_records) == 1
        assert store.failure_records[0].next_retry_at is None

    @staticmethod
    async def test_exhausted_message_logs_when_both_dead_letter_and_mark_failed_fail(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        store, _msg = _make_pending_store()
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        store.mark_failed_error = ConnectionError('mark_failed broken too')
        serializer = make_serializer(_TestEvent)

        with caplog.at_level(logging.ERROR, logger='waku.messaging.outbox.relay'):
            async with _run_relay(
                RelayDepsProvider(store, _FailingTransport(), serializer),
                _EXHAUST_ON_FIRST_FAILURE_CONFIG,
            ):
                pass

        assert 'Failed to mark message' in caplog.text

    @staticmethod
    async def test_stop_cancels_when_relay_does_not_terminate(caplog: pytest.LogCaptureFixture) -> None:
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        class _BlockingOutboxStore(_TrackingOutboxStore):
            @override
            async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
                await anyio.sleep_forever()
                return []  # pragma: no cover

        blocking_store = _BlockingOutboxStore()

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(hours=1),
            stop_timeout=0.05,
        )

        with caplog.at_level(logging.WARNING, logger='waku.messaging.outbox.relay'):
            async with make_async_container(RelayDepsProvider(blocking_store, transport, serializer)) as container:
                relay = OutboxRelay(
                    container=container,
                    config=config,
                    sending_failure_evaluator=make_relay_evaluator(config),
                )
                await relay.start()
                await anyio.sleep(0.02)
                await relay.stop()

        assert 'OutboxRelay did not terminate' in caplog.text

    @staticmethod
    async def test_stop_without_start_is_noop() -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        async with make_async_container(RelayDepsProvider(store, transport, serializer)) as container:
            relay = OutboxRelay(
                container=container,
                config=_FAST_CONFIG,
                sending_failure_evaluator=make_relay_evaluator(_FAST_CONFIG),
            )
            await relay.stop()

    @staticmethod
    async def test_recovers_stuck_messages_when_interval_elapsed(caplog: pytest.LogCaptureFixture) -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()
        serializer = make_serializer(_TestEvent)

        recovered_count = 5

        async def _recover_stuck_with_results(_threshold: timedelta) -> int:  # noqa: RUF029
            return recovered_count

        store.recover_stuck = _recover_stuck_with_results  # type: ignore[assignment]

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(seconds=0),
        )

        with caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'):
            async with _run_relay(RelayDepsProvider(store, transport, serializer), config, sleep=0.05):
                pass

        assert 'Recovered 5 stuck messages' in caplog.text

    @staticmethod
    async def test_discard_policy_marks_discarded() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={'test://dest': (SendingFailurePolicy.on_any_exception().discard(),)},
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            evaluator=evaluator,
        ):
            pass

        assert msg.id in store.discarded_ids
        assert msg.id not in store.failed_ids
        assert msg.id not in store.dead_lettered_ids

    @staticmethod
    async def test_dead_letter_outcome_dead_letters_immediately() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={'test://dest': (SendingFailurePolicy.on_any_exception().move_to_dead_letter(),)},
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            evaluator=evaluator,
        ):
            pass

        assert msg.id in store.dead_lettered_ids

    @staticmethod
    async def test_retry_with_backoff_policy_reschedules_with_future_next_retry_at(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pin the jittered backoff (whose floor is 0) to a fixed delay so the RETRY_WITH_BACKOFF arm is
        # observably distinct from the no-delay RETRY arm — otherwise a "schedule now" regression passes.
        monkeypatch.setattr('waku.messaging._escalation.calculate_backoff_with_jitter', lambda *_a, **_kw: 60.0)
        before = datetime.now(tz=UTC)
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={
                'test://dest': (
                    SendingFailurePolicy
                    .on_any_exception()
                    .retry_with_backoff(max_attempts=3)
                    .then_move_to_dead_letter(),
                )
            },
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            evaluator=evaluator,
        ):
            pass

        assert msg.id in store.failed_ids
        assert store.failure_records
        record = store.failure_records[0]
        assert record.next_retry_at is not None
        assert record.next_retry_at > before + timedelta(seconds=30)

    @staticmethod
    async def test_retry_policy_reschedules_for_next_poll() -> None:
        # Exercises the RETRY (no-backoff) arm of _apply_outcome: reschedule for the next poll
        # (next_retry_at≈now), NOT a future backoff delay.
        before = datetime.now(tz=UTC)
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={
                'test://dest': (
                    SendingFailurePolicy.on_any_exception().retry(max_attempts=2).then_move_to_dead_letter(),
                )
            },
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            evaluator=evaluator,
        ):
            pass

        assert msg.id in store.failed_ids
        assert store.failure_records
        record = store.failure_records[0]
        assert record.next_retry_at is not None
        assert before <= record.next_retry_at <= before + timedelta(seconds=5)

    @staticmethod
    async def test_missing_outcome_dead_letters_as_failsafe() -> None:
        # An empty evaluator (no synthesized default — only happens under misconfiguration) must
        # dead-letter rather than silently drop or infinite-retry a durable message.
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        empty_evaluator = SendingFailureEvaluator(
            registry=SendingFailurePolicyRegistry(destination_policies={}, default_policies=()),
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport(), serializer),
            evaluator=empty_evaluator,
        ):
            pass

        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert msg.id not in store.discarded_ids
