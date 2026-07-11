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

from waku.messages import IEvent
from waku.messaging import PollingConfig
from waku.messaging._escalation import RetryAction, walk_stages  # noqa: PLC2701
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig, build_relay_default_policy
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicy, SendingFailurePolicyRegistry
from waku.messaging.transport.decomposition import encode_metadata, encode_payload, wire_metadata_from_entry
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription

from tests._wait import wait_until
from tests.messaging.helpers import (
    RecordingTransport,
    RelayDepsProvider,
    StubSubscription,
    make_codec,
    make_envelope,
    make_relay_evaluator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    import pytest

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.transport.inbound import ConsumeCallback


@dataclass(frozen=True, slots=True)
class _TestEvent(IEvent):
    value: str


class _FailingTransport(ITransport):
    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        msg = 'transport down'
        raise ConnectionError(msg)

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        return StubSubscription()

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


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
    poll_calls: int = 0
    cleanup_calls: int = 0
    cleanup_count: int = 0
    move_to_dead_letter_error: Exception | None = None
    mark_failed_error: Exception | None = None
    recover_stuck_error: Exception | None = None

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:  # pragma: no cover
        self.pending.extend(messages)

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        # Relay tests stage non-partitioned messages, so head-of-queue is plain FIFO slicing.
        self.poll_calls += 1
        batch = self.pending[:batch_size]
        self.pending = self.pending[batch_size:]
        return batch

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
        if self.recover_stuck_error is not None:
            err = self.recover_stuck_error
            self.recover_stuck_error = None
            raise err
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        self.cleanup_calls += 1
        return self.cleanup_count


def _make_outbox_message(envelope: MessageEnvelope[Any], *, group_id: str | None = None) -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, make_codec()),
        metadata_=encode_metadata(envelope),
        destination='test://dest',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        group_id=group_id,
    )


def _make_pending_store(*, group_id: str | None = None) -> tuple[_TrackingOutboxStore, OutboxMessage]:
    store = _TrackingOutboxStore()
    envelope = make_envelope(_TestEvent(value='test'))
    msg = _make_outbox_message(envelope, group_id=group_id)
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
    evaluator: SendingFailureEvaluator | None = None,
) -> AsyncGenerator[None]:
    async with make_async_container(provider) as container:
        relay = OutboxRelay(
            container=container,
            config=config,
            sending_failure_evaluator=evaluator or make_relay_evaluator(config),
        )
        await relay.start()
        try:
            yield  # the caller awaits the effect it expects, then this CM stops the relay
        finally:
            await relay.stop()


class TestOutboxRelay:
    @staticmethod
    async def test_processes_pending_messages() -> None:
        store, msg = _make_pending_store()
        transport = RecordingTransport()
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert msg.id in store.dispatched_ids
        assert len(transport.sent) == 1
        body, destination, metadata, _mapper = transport.sent[0]
        assert destination == 'dest'
        # The transport receives the stored wire dict verbatim — no deserialize/reserialize round-trip.
        assert body is msg.payload
        assert metadata == wire_metadata_from_entry(msg)

    @staticmethod
    async def test_passes_group_id_to_transport_as_wire_metadata() -> None:
        # The relay sources the partition-routing key off the OutboxMessage column — the transport (Kafka)
        # reads it as the message key; nothing parses the wire body for it.
        store, msg = _make_pending_store(group_id='order-1')
        transport = RecordingTransport()
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert transport.sent
        _body, _destination, metadata, _mapper = transport.sent[0]
        assert metadata.group_id == 'order-1'

    @staticmethod
    async def test_marks_failed_on_transport_error() -> None:
        store, msg = _make_pending_store()
        async with _run_relay(RelayDepsProvider(store, _FailingTransport())):
            await wait_until(lambda: msg.id in store.failed_ids)

        assert msg.id in store.failed_ids

    @staticmethod
    async def test_recovery_failure_does_not_crash_loop() -> None:
        # A recovery-backend failure does not crash the relay loop: the worker logs it, continues, and
        # still dispatches pending work on a later poll.
        store, msg = _make_pending_store()
        store.recover_stuck_error = ConnectionError('recovery backend down')
        transport = RecordingTransport()
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: store.recovered >= 1 and msg.id in store.dispatched_ids)

        assert store.recovered >= 1
        assert msg.id in store.dispatched_ids

    @staticmethod
    async def test_no_messages_is_noop() -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()

        # Asserting an absence: wait for one full poll cycle (the relay actually ran), then confirm
        # nothing was sent. No positive effect exists to await, so the poll counter is the gate.
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: store.poll_calls >= 1)

        assert len(transport.sent) == 0
        assert len(store.dispatched_ids) == 0

    @staticmethod
    async def test_stop_cancels_sleep_immediately() -> None:
        store = _TrackingOutboxStore()
        transport = RecordingTransport()

        slow_config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=10.0),
            recovery_interval=timedelta(hours=1),
        )

        async with make_async_container(RelayDepsProvider(store, transport)) as container:
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

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            await wait_until(lambda: msg.id in store.dead_lettered_ids)

        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert len(store.dead_letter_entries) == 1
        entry = store.dead_letter_entries[0]
        assert isinstance(entry, DeadLetterEntry)
        assert entry.destination == 'test://dest'
        assert entry.retry_count == 1

    @staticmethod
    async def test_exhausted_relay_dead_letter_entry_carries_metadata_and_group_id() -> None:
        store, msg = _make_pending_store(group_id='order-77')

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            await wait_until(lambda: msg.id in store.dead_lettered_ids)

        assert len(store.dead_letter_entries) == 1
        entry = store.dead_letter_entries[0]
        assert entry.metadata_ == msg.metadata_
        assert entry.group_id == msg.group_id

    @staticmethod
    async def test_exhausted_message_falls_back_to_mark_failed_when_move_to_dead_letter_raises() -> None:
        store, msg = _make_pending_store()
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            await wait_until(lambda: msg.id in store.failed_ids)

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

        with caplog.at_level(logging.ERROR, logger='waku.messaging.outbox.relay'):
            async with _run_relay(
                RelayDepsProvider(store, _FailingTransport()),
                _EXHAUST_ON_FIRST_FAILURE_CONFIG,
            ):
                await wait_until(lambda: 'Failed to mark message' in caplog.text)

        assert 'Failed to mark message' in caplog.text

    @staticmethod
    async def test_stop_cancels_when_relay_does_not_terminate(caplog: pytest.LogCaptureFixture) -> None:
        transport = RecordingTransport()

        class _BlockingOutboxStore(_TrackingOutboxStore):
            @override
            async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
                await anyio.sleep_forever()
                return []  # pragma: no cover

        blocking_store = _BlockingOutboxStore()

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(hours=1),
            stop_timeout=0.05,
        )

        with caplog.at_level(logging.WARNING, logger='waku.messaging.outbox.relay'):
            async with make_async_container(RelayDepsProvider(blocking_store, transport)) as container:
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

        async with make_async_container(RelayDepsProvider(store, transport)) as container:
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

        recovered_count = 5

        async def _recover_stuck_with_results(_threshold: timedelta) -> int:  # noqa: RUF029
            return recovered_count

        store.recover_stuck = _recover_stuck_with_results  # type: ignore[assignment]

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(seconds=0),
        )

        with caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'):
            async with _run_relay(RelayDepsProvider(store, transport), config):
                await wait_until(lambda: 'Recovered 5 stuck messages' in caplog.text)

        assert 'Recovered 5 stuck messages' in caplog.text

    @staticmethod
    async def test_purges_dispatched_messages_when_retention_elapsed(caplog: pytest.LogCaptureFixture) -> None:
        store = _TrackingOutboxStore(cleanup_count=3)
        transport = RecordingTransport()

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            retention=timedelta(hours=1),
            cleanup_interval=timedelta(seconds=0),
        )

        with caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'):
            async with _run_relay(RelayDepsProvider(store, transport), config):
                await wait_until(lambda: 'Purged 3 dispatched outbox messages older than retention' in caplog.text)

        assert 'Purged 3 dispatched outbox messages older than retention' in caplog.text

    @staticmethod
    async def test_does_not_purge_dispatched_messages_when_retention_unset() -> None:
        store = _TrackingOutboxStore(cleanup_count=3)
        transport = RecordingTransport()

        config = OutboxRelayConfig(polling=PollingConfig(poll_interval_min_seconds=0.01))

        async with _run_relay(RelayDepsProvider(store, transport), config):
            await wait_until(lambda: store.poll_calls >= 1)

        assert store.cleanup_calls == 0

    @staticmethod
    async def test_discard_policy_marks_discarded() -> None:
        store, msg = _make_pending_store()
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={'test://dest': (SendingFailurePolicy.on_any_exception().discard(),)},
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            evaluator=evaluator,
        ):
            await wait_until(lambda: msg.id in store.discarded_ids)

        assert msg.id in store.discarded_ids
        assert msg.id not in store.failed_ids
        assert msg.id not in store.dead_lettered_ids

    @staticmethod
    async def test_dead_letter_outcome_dead_letters_immediately() -> None:
        store, msg = _make_pending_store()
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={'test://dest': (SendingFailurePolicy.on_any_exception().move_to_dead_letter(),)},
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            evaluator=evaluator,
        ):
            await wait_until(lambda: msg.id in store.dead_lettered_ids)

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
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={
                'test://dest': (
                    SendingFailurePolicy
                    .on_any_exception()
                    .retry_with_backoff(max_attempts=3)
                    .then_move_to_dead_letter(),
                ),
            },
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            evaluator=evaluator,
        ):
            await wait_until(lambda: msg.id in store.failed_ids)

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
        evaluator = make_relay_evaluator(
            _FAST_CONFIG,
            destination_policies={
                'test://dest': (
                    SendingFailurePolicy.on_any_exception().retry(max_attempts=2).then_move_to_dead_letter(),
                ),
            },
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            evaluator=evaluator,
        ):
            await wait_until(lambda: msg.id in store.failed_ids)

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
        empty_evaluator = SendingFailureEvaluator(
            registry=SendingFailurePolicyRegistry(destination_policies={}, default_policies=()),
        )

        async with _run_relay(
            RelayDepsProvider(store, _FailingTransport()),
            evaluator=empty_evaluator,
        ):
            await wait_until(lambda: msg.id in store.dead_lettered_ids)

        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert msg.id not in store.discarded_ids


class TestDispatchMessageMetadata:
    @staticmethod
    async def test_relay_sends_full_envelope_metadata_from_decomposed_row() -> None:
        # Round-trip contract: encode_metadata(envelope) stored → wire_metadata_from_entry(row) reconstructed.
        # The relay must forward the full 5 non-column fields (message_version, timestamp, headers,
        # scheduled_time, expires_at) alongside the typed columns — this was the ASYM-1 gap.
        envelope = make_envelope(
            _TestEvent(value='round-trip'),
            headers={'x-tenant': 'acme'},
            scheduled_time=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            expires_at=datetime(2025, 1, 2, 0, 0, 0, tzinfo=UTC),
        )
        msg = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=encode_payload(envelope, make_codec()),
            metadata_=encode_metadata(envelope),
            destination='test://dest',
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
        )
        store = _TrackingOutboxStore()
        store.pending.append(msg)
        transport = RecordingTransport()
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert len(transport.sent) == 1
        body, destination, metadata, _mapper = transport.sent[0]
        assert body is msg.payload
        assert destination == 'dest'
        assert metadata.message_version == envelope.message_version
        assert metadata.timestamp is not None
        assert metadata.timestamp.isoformat() == envelope.timestamp.isoformat()
        assert metadata.headers == {'x-tenant': 'acme'}
        assert metadata.scheduled_time is not None
        assert metadata.scheduled_time.isoformat() == envelope.scheduled_time.isoformat()  # type: ignore[union-attr]
        assert metadata.expires_at is not None
        assert metadata.expires_at.isoformat() == envelope.expires_at.isoformat()  # type: ignore[union-attr]
        # Typed columns — these live on the row, NOT in metadata_, and must reach the transport.
        assert metadata.correlation_id == str(envelope.correlation_id)
        assert metadata.causation_id == str(envelope.causation_id)
        assert metadata.message_id == str(envelope.message_id)
        assert metadata.message_type == envelope.message_type
        assert metadata.group_id == envelope.group_id


def test_build_relay_default_policy_is_catch_all_backoff_then_dead_letter() -> None:
    policy = build_relay_default_policy(OutboxRelayConfig(max_attempts=5, base_delay=2.0, max_delay=30.0))
    assert isinstance(policy, SendingFailurePolicy)
    assert policy.exception_type is None
    assert policy.predicate is None
    assert [s.action for s in policy.stages] == [RetryAction.RETRY_WITH_BACKOFF, RetryAction.DEAD_LETTER]
    assert policy.stages[0].max_attempts == 5
    assert policy.stages[0].base_delay == 2.0
    assert policy.stages[0].max_delay == 30.0


# Observable identity mapper — map_outgoing returns payload unchanged; used to assert the override reached send.
class _MarkerMapper(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> Any:
        return payload  # pragma: no cover -- only referenced; FastStreamKafkaTransport calls it, RecordingTransport does not

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raise NotImplementedError  # pragma: no cover


class TestRelayMapperOverrideWiring:
    @staticmethod
    async def test_per_route_mapper_override_reaches_sender_send() -> None:
        # The critical end-to-end wiring proof:
        # BrokerEndpointEntry.send.mapper → _build_transport_registry → TransportRegistry.mapper_for
        # → OutboxRelay._dispatch_message → RecordingTransport.send(mapper=override).
        # Observable via the 4th element of the recording tuple — not mock internals.
        override_mapper = _MarkerMapper()
        store, msg = _make_pending_store()
        # Destination is 'test://dest' (set by _make_outbox_message); configure override for that full URI.
        transport = RecordingTransport()
        async with _run_relay(
            RelayDepsProvider(store, transport, external_mappers={'test://dest': override_mapper}),
        ):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert len(transport.sent) == 1
        _body, _destination, _metadata, mapper = transport.sent[0]
        # The override mapper instance must be exactly the one configured — proves the override flowed through
        # registry.mapper_for → relay → sender.send, not just a per-transport default.
        assert mapper is override_mapper

    @staticmethod
    async def test_no_override_configured_sends_none_as_mapper() -> None:
        # A route with NO BrokerEndpointEntry.send.mapper configured → registry.mapper_for returns None
        # → sender.send(mapper=None) so the transport falls back to its default.
        store, msg = _make_pending_store()
        transport = RecordingTransport()
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert len(transport.sent) == 1
        _body, _destination, _metadata, mapper = transport.sent[0]
        assert mapper is None
