from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator  # noqa: TC003  # Dishka inspects provider return annotations
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import anyio
import anyio.lowlevel
import pytest
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import AfterCommitError, RollbackFailedError, TransactionExecutionError
from waku.messages import IEvent
from waku.messaging import PollingConfig
from waku.messaging._internal.escalation import RetryAction, walk_stages
from waku.messaging.durability import IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig, build_relay_default_policy
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicy, SendingFailurePolicyRegistry
from waku.messaging.transport._internal.registry import TransportRegistry
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload, wire_metadata_from_entry
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.uow import IUnitOfWork

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
    from collections.abc import AsyncGenerator, Callable, Sequence

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.transport.inbound import ConsumeCallback


@dataclass(frozen=True, slots=True)
class _TestEvent(IEvent):
    value: str


class _FailingTransport(ITransport):
    def __init__(self, trace: list[str] | None = None) -> None:
        self._trace = trace

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        if self._trace is not None:
            self._trace.append('broker-send')
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
class _RecordingOutboxStore(IOutboxStore):
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
    move_to_dead_letter_error: BaseException | None = None
    mark_failed_error: Exception | None = None
    recover_abandoned_error: Exception | None = None
    mark_dispatched_error: Exception | None = None
    trace: list[str] | None = None

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:  # pragma: no cover
        self.pending.extend(messages)

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        # Relay tests stage non-partitioned messages, so head-of-queue is plain FIFO slicing.
        if self.trace is not None:
            self.trace.append('fetch')
        self.poll_calls += 1
        batch = self.pending[:batch_size]
        self.pending = self.pending[batch_size:]
        return batch

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        if self.trace is not None:
            self.trace.append('mark-dispatched')
        if self.mark_dispatched_error is not None:
            err = self.mark_dispatched_error
            self.mark_dispatched_error = None
            raise err
        self.dispatched_ids.append(message_id)

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        if self.trace is not None:
            self.trace.append('mark-failed')
        if self.mark_failed_error is not None:
            raise self.mark_failed_error
        self.failed_ids.append(message_id)
        self.failure_records.append(_FailureRecord(message_id=message_id, error=error, next_retry_at=next_retry_at))

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:
        if self.trace is not None:
            self.trace.append('move-to-dead-letter')
        if self.move_to_dead_letter_error is not None:
            raise self.move_to_dead_letter_error
        self.dead_lettered_ids.append(message_id)
        self.dead_letter_entries.append(entry)

    @override
    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        if self.trace is not None:
            self.trace.append('mark-discarded')
        self.discarded_ids.append(message_id)

    @override
    async def recover_abandoned(self, threshold: timedelta) -> int:
        self.recovered += 1
        if self.recover_abandoned_error is not None:
            err = self.recover_abandoned_error
            self.recover_abandoned_error = None
            raise err
        return 0

    @override
    async def delete_expired_dispatched(self, older_than: timedelta, *, now: datetime) -> int:
        self.cleanup_calls += 1
        return self.cleanup_count


class _TracingUoW(IUnitOfWork):
    def __init__(
        self,
        trace: list[str],
        *,
        commit_error_at: int | None = None,
        commit_error: BaseException | None = None,
        commit_labels: dict[int, str] | None = None,
        rollback_error_at: int | None = None,
        rollback_error: BaseException | None = None,
        rollback_label: str = 'rollback',
    ) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self._trace = trace
        self._commit_error_at = commit_error_at
        self._commit_error = commit_error
        self._commit_labels = commit_labels or {}
        self._rollback_error_at = rollback_error_at
        self._rollback_error = rollback_error
        self._rollback_label = rollback_label

    @override
    async def commit(self) -> None:
        self.commit_count += 1
        self._trace.append(self._commit_labels.get(self.commit_count, 'commit'))
        if self.commit_count == self._commit_error_at:
            if self._commit_error is None:  # pragma: no cover - invalid test-double setup
                msg = 'commit_error is required when commit_error_at is set'
                raise RuntimeError(msg)
            raise self._commit_error

    @override
    async def rollback(self) -> None:
        self.rollback_count += 1
        await anyio.lowlevel.checkpoint()
        self._trace.append(self._rollback_label)
        if self.rollback_count == self._rollback_error_at:
            if self._rollback_error is None:  # pragma: no cover - invalid test-double setup
                msg = 'rollback_error is required when rollback_error_at is set'
                raise RuntimeError(msg)
            raise self._rollback_error


class _TracingTransport(RecordingTransport):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self._trace = trace

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        self._trace.append('broker-send')
        await super().send(body, destination=destination, metadata=metadata, mapper=mapper)


class _PhaseDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: IOutboxStore,
        transport: ITransport,
        trace: list[str],
        *,
        uow_factory: Callable[[int], IUnitOfWork] | None = None,
        store_exit_error_at: int | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._registry = TransportRegistry({'test': transport})
        self._trace = trace
        self._uow_factory = uow_factory
        self._store_exit_error_at = store_exit_error_at
        self._store_scope_count = 0
        self.uows: list[IUnitOfWork] = []

    @provide
    async def outbox_store(self) -> AsyncIterator[IOutboxStore]:
        self._store_scope_count += 1
        phase = self._store_scope_count
        try:
            yield self._store
        finally:
            self._trace.append(f'phase-{phase}:exit')
            if phase == self._store_exit_error_at:
                msg = f'phase-{phase} scope exit failed'
                raise RuntimeError(msg)

    @provide
    def uow(self) -> IUnitOfWork:
        phase = len(self.uows) + 1
        self._trace.append(f'phase-{phase}:begin')
        uow = (
            self._uow_factory(phase)
            if self._uow_factory is not None
            else _TracingUoW(
                self._trace,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_label=f'phase-{phase}:rollback',
            )
        )
        self.uows.append(uow)
        return uow

    @provide(scope=Scope.APP)
    def transport_registry(self) -> TransportRegistry:
        return self._registry


def _make_outbox_message(envelope: MessageEnvelope[Any], *, group_id: str | None = None) -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, make_codec()),
        metadata=encode_metadata(envelope),
        destination='test://dest',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        group_id=group_id,
    )


def _make_pending_store(*, group_id: str | None = None) -> tuple[_RecordingOutboxStore, OutboxMessage]:
    store = _RecordingOutboxStore()
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
    # Pins behavior-equivalence with the legacy fixed loop for N>1: with relay attempt = attempts+1,
    # retries at attempts 1..N-1, dead-letters at attempt N. A mutation to walk_stages' boundary
    # (< vs <=) is caught here.
    stages = build_relay_default_policy(OutboxRelayConfig(max_attempts=2)).stages
    assert walk_stages(stages, attempt=1).action is RetryAction.RETRY_WITH_BACKOFF
    assert walk_stages(stages, attempt=2).action is RetryAction.DEAD_LETTER


@asynccontextmanager
async def _run_relay(
    provider: Provider,
    config: OutboxRelayConfig = _FAST_CONFIG,
    *,
    evaluator: SendingFailureEvaluator | None = None,
    now: Now = utc_now,
) -> AsyncGenerator[None]:
    async with make_async_container(provider) as container:
        relay = OutboxRelay(
            container=container,
            config=config,
            sending_failure_evaluator=evaluator or make_relay_evaluator(config),
            now=now,
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


class TestOutboxRelaySendFailureOwnership:
    @staticmethod
    async def test_success_uses_fresh_claim_and_record_phases_around_broker_send() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        provider = _PhaseDepsProvider(store, _TracingTransport(trace), trace)

        async with _run_relay(provider):
            await wait_until(lambda: msg.id in store.dispatched_ids)

        assert len(provider.uows) == 2
        assert trace == [
            'phase-1:begin',
            'fetch',
            'phase-1:commit',
            'phase-1:exit',
            'broker-send',
            'phase-2:begin',
            'mark-dispatched',
            'phase-2:commit',
            'phase-2:exit',
        ]

    @staticmethod
    async def test_send_failure_retry_uses_fresh_policy_phase_after_broker_send() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace)

        async with _run_relay(provider):
            await wait_until(lambda: msg.id in store.failed_ids)

        assert len(provider.uows) == 2
        assert trace == [
            'phase-1:begin',
            'fetch',
            'phase-1:commit',
            'phase-1:exit',
            'broker-send',
            'phase-2:begin',
            'mark-failed',
            'phase-2:commit',
            'phase-2:exit',
        ]

    @staticmethod
    async def test_claim_transaction_fatal_stops_before_broker_or_policy() -> None:
        trace: list[str] = []
        store, _msg = _make_pending_store()
        store.trace = trace
        commit_error = RuntimeError('claim commit failed')
        rollback_error = RuntimeError('claim rollback failed')

        def uow_factory(phase: int) -> IUnitOfWork:
            return _TracingUoW(
                trace,
                commit_error_at=1 if phase == 1 else None,
                commit_error=commit_error,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_error_at=1 if phase == 1 else None,
                rollback_error=rollback_error,
                rollback_label=f'phase-{phase}:rollback',
            )

        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace, uow_factory=uow_factory)
        with pytest.raises(TransactionExecutionError) as raised:
            async with _run_relay(provider):
                await wait_until(lambda: 'phase-1:rollback' in trace)

        assert isinstance(raised.value, RollbackFailedError)
        assert raised.value.error is rollback_error
        assert store.poll_calls == 1
        assert 'broker-send' not in trace
        assert 'mark-failed' not in trace


class TestOutboxRelayOperations:
    @staticmethod
    async def test_tick_only_dispatches_no_recover_or_cleanup() -> None:
        # Dispatch-only relay (D9): recover_abandoned/delete_expired_dispatched moved to DurabilityMaintenanceAgent.
        # Even with the eager recovery/cleanup intervals set, the relay never touches them — only
        # fetch_head_of_queue runs.
        store = _RecordingOutboxStore(cleanup_count=3)
        transport = RecordingTransport()
        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(seconds=0),
            retention=timedelta(hours=1),
            cleanup_interval=timedelta(seconds=0),
        )

        async with _run_relay(RelayDepsProvider(store, transport), config):
            await wait_until(lambda: store.poll_calls >= 1)

        assert store.recovered == 0  # recover_abandoned never called
        assert store.cleanup_calls == 0  # delete_expired_dispatched never called
        assert store.poll_calls >= 1  # fetch_head_of_queue WAS called

    @staticmethod
    async def test_no_messages_is_noop() -> None:
        store = _RecordingOutboxStore()
        transport = RecordingTransport()

        # Asserting an absence: wait for one full poll cycle (the relay actually ran), then confirm
        # nothing was sent. No positive effect exists to await, so the poll counter is the gate.
        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: store.poll_calls >= 1)

        assert len(transport.sent) == 0
        assert len(store.dispatched_ids) == 0

    @staticmethod
    async def test_stop_cancels_sleep_immediately() -> None:
        store = _RecordingOutboxStore()
        transport = RecordingTransport()

        slow_config = OutboxRelayConfig(
            polling=PollingConfig(
                poll_interval_min_seconds=10.0,
                poll_interval_max_seconds=10.0,
            ),
            recovery_interval=timedelta(hours=1),
        )

        async with make_async_container(RelayDepsProvider(store, transport)) as container:
            relay = OutboxRelay(
                container=container,
                config=slow_config,
                sending_failure_evaluator=make_relay_evaluator(slow_config),
            )
            await relay.start()
            await wait_until(lambda: store.poll_calls >= 1)
            await asyncio.wait_for(relay.stop(), timeout=1.0)

    @staticmethod
    async def test_exhausted_message_counts_and_logs_only_after_primary_commit(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace)

        with caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: msg.id in store.dead_lettered_ids)

        assert len(provider.uows) == 2
        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert len(store.dead_letter_entries) == 1
        entry = store.dead_letter_entries[0]
        assert isinstance(entry, DeadLetterEntry)
        assert entry.destination == 'test://dest'
        assert entry.retry_count == 1
        assert trace == [
            'phase-1:begin',
            'fetch',
            'phase-1:commit',
            'phase-1:exit',
            'broker-send',
            'phase-2:begin',
            'move-to-dead-letter',
            'phase-2:commit',
            'phase-2:exit',
        ]
        assert 'moved to dead letter after 1 attempts' in caplog.text

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
        assert entry.metadata == msg.metadata
        assert entry.group_id == msg.group_id

    @staticmethod
    async def test_exhausted_message_falls_back_to_mark_failed_when_move_to_dead_letter_raises() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace)

        async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
            await wait_until(lambda: msg.id in store.failed_ids)

        assert len(provider.uows) == 3
        assert msg.id in store.failed_ids
        assert msg.id not in store.dead_lettered_ids
        assert len(store.failure_records) == 1
        assert store.failure_records[0].next_retry_at is None
        assert trace == [
            'phase-1:begin',
            'fetch',
            'phase-1:commit',
            'phase-1:exit',
            'broker-send',
            'phase-2:begin',
            'move-to-dead-letter',
            'phase-2:rollback',
            'phase-2:exit',
            'phase-3:begin',
            'mark-failed',
            'phase-3:commit',
            'phase-3:exit',
        ]

    @staticmethod
    async def test_fallback_mutation_error_rolls_back_and_tick_continues_without_terminal_evidence(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        trace: list[str] = []
        store, _msg = _make_pending_store()
        store.trace = trace
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        store.mark_failed_error = ConnectionError('mark_failed broken too')
        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace)

        with caplog.at_level(logging.INFO):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'OutboxRelay tick failed, continuing loop' in caplog.text)

        assert 'Failed to mark message' in caplog.text
        assert 'OutboxRelay tick failed, continuing loop' in caplog.text
        assert caplog.text.count('exhausted after') == 0
        assert caplog.text.count('moved to dead letter') == 0
        assert store.failed_ids == []
        assert store.dead_lettered_ids == []

    @staticmethod
    async def test_fallback_commit_failure_rolls_back_and_tick_continues_without_terminal_evidence(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        trace: list[str] = []
        store, _msg = _make_pending_store()
        store.trace = trace
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        commit_error = RuntimeError('fallback commit failed')

        def uow_factory(phase: int) -> IUnitOfWork:
            return _TracingUoW(
                trace,
                commit_error_at=1 if phase == 3 else None,
                commit_error=commit_error,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_label=f'phase-{phase}:rollback',
            )

        provider = _PhaseDepsProvider(
            store,
            _FailingTransport(trace),
            trace,
            uow_factory=uow_factory,
        )
        with caplog.at_level(logging.INFO):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'OutboxRelay tick failed, continuing loop' in caplog.text)

        assert trace[-4:] == ['mark-failed', 'phase-3:commit', 'phase-3:rollback', 'phase-3:exit']
        assert 'OutboxRelay tick failed, continuing loop' in caplog.text
        assert caplog.text.count('exhausted after') == 0
        assert caplog.text.count('moved to dead letter') == 0
        assert store.dead_lettered_ids == []

    @staticmethod
    async def test_exhausted_does_not_start_fallback_when_primary_rollback_fails() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        store.pending[:] = [replace(msg, metadata={'message_version': 'abc', 'timestamp': None, 'headers': {}})]
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        rollback_error = RuntimeError('rollback failed')

        def uow_factory(phase: int) -> IUnitOfWork:
            return _TracingUoW(
                trace,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_error_at=1 if phase == 2 else None,
                rollback_error=rollback_error,
                rollback_label=f'phase-{phase}:rollback',
            )

        provider = _PhaseDepsProvider(store, RecordingTransport(), trace, uow_factory=uow_factory)
        with pytest.raises(TransactionExecutionError) as raised:
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'phase-2:rollback' in trace)

        assert isinstance(raised.value, RollbackFailedError)
        assert raised.value.error is rollback_error
        assert len(provider.uows) == 2
        assert 'mark-failed' not in trace

    @staticmethod
    async def test_primary_after_commit_evidence_escapes_without_fallback_or_terminal_log(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        store.pending[:] = [replace(msg, metadata={'message_version': 'abc', 'timestamp': None, 'headers': {}})]
        provider = _PhaseDepsProvider(store, RecordingTransport(), trace, store_exit_error_at=2)

        with (
            caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'),
            pytest.raises(TransactionExecutionError) as raised,
        ):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'phase-2:exit' in trace)

        assert isinstance(raised.value, AfterCommitError)
        assert len(provider.uows) == 2
        assert 'mark-failed' not in trace
        assert 'moved to dead letter' not in caplog.text

    @staticmethod
    async def test_primary_cancellation_rolls_back_and_suppresses_fallback() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        store.pending[:] = [replace(msg, metadata={'message_version': 'abc', 'timestamp': None, 'headers': {}})]
        store.move_to_dead_letter_error = asyncio.CancelledError()
        provider = _PhaseDepsProvider(store, RecordingTransport(), trace)

        with pytest.raises(asyncio.CancelledError):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'phase-2:rollback' in trace)

        assert len(provider.uows) == 2
        assert 'mark-failed' not in trace

    @staticmethod
    async def test_fallback_rollback_failure_escapes_without_terminal_evidence(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        trace: list[str] = []
        store, _msg = _make_pending_store()
        store.trace = trace
        store.move_to_dead_letter_error = ConnectionError('DLQ store unavailable')
        store.mark_failed_error = ConnectionError('fallback mutation failed')
        rollback_error = RuntimeError('fallback rollback failed')

        def uow_factory(phase: int) -> IUnitOfWork:
            return _TracingUoW(
                trace,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_error_at=1 if phase == 3 else None,
                rollback_error=rollback_error,
                rollback_label=f'phase-{phase}:rollback',
            )

        provider = _PhaseDepsProvider(store, _FailingTransport(trace), trace, uow_factory=uow_factory)
        with (
            caplog.at_level(logging.INFO, logger='waku.messaging.outbox.relay'),
            pytest.raises(TransactionExecutionError) as raised,
        ):
            async with _run_relay(provider, _EXHAUST_ON_FIRST_FAILURE_CONFIG):
                await wait_until(lambda: 'phase-3:rollback' in trace)

        assert isinstance(raised.value, RollbackFailedError)
        assert raised.value.error is rollback_error
        assert 'exhausted after' not in caplog.text
        assert 'moved to dead letter' not in caplog.text

    @staticmethod
    async def test_stop_cancels_when_relay_does_not_terminate(caplog: pytest.LogCaptureFixture) -> None:
        transport = RecordingTransport()

        class _BlockingOutboxStore(_RecordingOutboxStore):
            def __init__(self) -> None:
                super().__init__()
                self.fetch_entered = anyio.Event()

            @override
            async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
                self.fetch_entered.set()
                await anyio.sleep_forever()
                return []  # pragma: no cover

        blocking_store = _BlockingOutboxStore()

        config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01),
            recovery_interval=timedelta(hours=1),
            stop_timeout=timedelta(seconds=0.05),
        )

        with caplog.at_level(logging.WARNING, logger='waku.messaging.outbox.relay'):
            async with make_async_container(RelayDepsProvider(blocking_store, transport)) as container:
                relay = OutboxRelay(
                    container=container,
                    config=config,
                    sending_failure_evaluator=make_relay_evaluator(config),
                )
                await relay.start()
                await wait_until(blocking_store.fetch_entered.is_set)
                await relay.stop()

        assert 'OutboxRelay did not terminate' in caplog.text

    @staticmethod
    async def test_stop_without_start_is_noop() -> None:
        store = _RecordingOutboxStore()
        transport = RecordingTransport()

        async with make_async_container(RelayDepsProvider(store, transport)) as container:
            relay = OutboxRelay(
                container=container,
                config=_FAST_CONFIG,
                sending_failure_evaluator=make_relay_evaluator(_FAST_CONFIG),
            )
            await relay.stop()

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
        monkeypatch.setattr(
            'waku.messaging._internal.escalation.calculate_backoff_with_jitter', lambda *_a, **_kw: 60.0
        )
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
            now=lambda: _FIXED_NOW,
        ):
            await wait_until(lambda: msg.id in store.failed_ids)

        assert msg.id in store.failed_ids
        assert store.failure_records
        record = store.failure_records[0]
        # RETRY_WITH_BACKOFF schedules off the relay's injected clock: next_retry_at = now + backoff (pinned 60s).
        assert record.next_retry_at == _FIXED_NOW + timedelta(seconds=60)

    @staticmethod
    async def test_retry_policy_reschedules_for_next_poll() -> None:
        # Exercises the RETRY (no-backoff) arm of _apply_outcome: reschedule for the next poll
        # (next_retry_at == the relay's injected clock), NOT a future backoff delay.
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
            now=lambda: _FIXED_NOW,
        ):
            await wait_until(lambda: msg.id in store.failed_ids)

        assert msg.id in store.failed_ids
        assert store.failure_records
        record = store.failure_records[0]
        assert record.next_retry_at == _FIXED_NOW

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


class TestRelayDispatchQuarantine:
    # The two delivered-or-poison quarantine invariants: a delivered message never becomes terminal
    # via the sending policy, and a poison row dead-letters instead of crash-looping the tick.

    @staticmethod
    async def test_post_send_persistence_failure_is_not_recorded_terminal() -> None:
        # The message WAS delivered; only recording failed. It must stay claimed (PROCESSING) for
        # recover_abandoned — never routed into the sending-failure policy (FAILED/DISCARDED/DEAD_LETTERED),
        # which would let a DLQ replay double-deliver.
        store, msg = _make_pending_store()
        store.mark_dispatched_error = ConnectionError('db down after send')
        transport = RecordingTransport()

        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: len(transport.sent) == 1 and store.poll_calls >= 2)

        assert len(transport.sent) == 1  # delivered exactly once — no policy-driven resend
        assert msg.id not in store.dispatched_ids
        assert msg.id not in store.failed_ids
        assert msg.id not in store.discarded_ids
        assert msg.id not in store.dead_lettered_ids
        assert not store.pending  # never re-enqueued PENDING; stays claimed for recover_abandoned

    @staticmethod
    async def test_post_send_persistence_failure_rolls_back_exactly_once() -> None:
        trace: list[str] = []
        store, msg = _make_pending_store()
        store.trace = trace
        store.mark_dispatched_error = ConnectionError('db down after send')
        provider = _PhaseDepsProvider(store, _TracingTransport(trace), trace)

        async with _run_relay(provider):
            await wait_until(lambda: 'phase-2:rollback' in trace)

        assert msg.id not in store.dispatched_ids
        assert 'mark-failed' not in trace
        assert trace == [
            'phase-1:begin',
            'fetch',
            'phase-1:commit',
            'phase-1:exit',
            'broker-send',
            'phase-2:begin',
            'mark-dispatched',
            'phase-2:rollback',
            'phase-2:exit',
        ]

    @staticmethod
    async def test_relay_cancellation_during_transaction_completes_rollback() -> None:
        trace: list[str] = []
        store, _msg = _make_pending_store()
        store.trace = trace
        cancellation = asyncio.CancelledError()

        def uow_factory(phase: int) -> IUnitOfWork:
            return _TracingUoW(
                trace,
                commit_error_at=1 if phase == 2 else None,
                commit_error=cancellation,
                commit_labels={1: f'phase-{phase}:commit'},
                rollback_label=f'phase-{phase}:rollback',
            )

        provider = _PhaseDepsProvider(store, _TracingTransport(trace), trace, uow_factory=uow_factory)
        with pytest.raises(asyncio.CancelledError) as raised:
            async with _run_relay(provider):
                await wait_until(lambda: 'phase-2:rollback' in trace)

        assert raised.value is cancellation
        assert trace[-3:] == ['phase-2:commit', 'phase-2:rollback', 'phase-2:exit']
        assert 'mark-failed' not in trace

    @staticmethod
    async def test_relay_dead_letters_corrupt_metadata_blob_without_sending() -> None:
        # A row whose persisted metadata blob is corrupt (non-integer message_version) is deterministic
        # poison, not a transient send failure: it dead-letters immediately and the broker is never
        # touched — no send-retry budget burned.
        store, msg = _make_pending_store()
        corrupt = replace(msg, metadata={'message_version': 'abc', 'timestamp': None, 'headers': {}})
        store.pending[:] = [corrupt]
        transport = RecordingTransport()

        async with _run_relay(RelayDepsProvider(store, transport)):
            await wait_until(lambda: corrupt.id in store.dead_lettered_ids)

        assert corrupt.id in store.dead_lettered_ids
        assert transport.sent == []  # broker never touched — corruption is not a send failure
        assert not store.pending


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
            expires_at=datetime(2999, 1, 2, 0, 0, 0, tzinfo=UTC),
        )
        msg = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=encode_payload(envelope, make_codec()),
            metadata=encode_metadata(envelope),
            destination='test://dest',
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
        )
        store = _RecordingOutboxStore()
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
        assert envelope.scheduled_time is not None
        assert metadata.scheduled_time.isoformat() == envelope.scheduled_time.isoformat()
        assert metadata.expires_at is not None
        assert envelope.expires_at is not None
        assert metadata.expires_at.isoformat() == envelope.expires_at.isoformat()
        # Typed columns — these live on the row, NOT in metadata, and must reach the transport.
        assert metadata.correlation_id == envelope.correlation_id
        assert metadata.causation_id == envelope.causation_id
        assert metadata.message_id == str(envelope.message_id)
        assert metadata.message_type == envelope.message_type
        assert metadata.group_id == envelope.group_id


def test_build_relay_default_policy_is_catch_all_backoff_then_dead_letter() -> None:
    policy = build_relay_default_policy(
        OutboxRelayConfig(max_attempts=5, base_delay=timedelta(seconds=2), max_delay=timedelta(seconds=30)),
    )
    assert isinstance(policy, SendingFailurePolicy)
    assert policy.exception_type is None
    assert policy.predicate is None
    assert [s.action for s in policy.stages] == [RetryAction.RETRY_WITH_BACKOFF, RetryAction.DEAD_LETTER]
    assert policy.stages[0].max_attempts == 5
    assert policy.stages[0].base_delay == timedelta(seconds=2)
    assert policy.stages[0].max_delay == timedelta(seconds=30)


def test_outbox_relay_config_delays_are_timedelta() -> None:
    config = OutboxRelayConfig()
    assert config.base_delay == timedelta(seconds=1)
    assert config.max_delay == timedelta(seconds=60)


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


_FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_PAST = _FIXED_NOW - timedelta(minutes=5)
_FUTURE = _FIXED_NOW + timedelta(minutes=5)


async def _assert_relay_ships(envelope: MessageEnvelope[Any]) -> None:
    store = _RecordingOutboxStore()
    msg = _make_outbox_message(envelope)
    store.pending.append(msg)
    transport = RecordingTransport()

    async with _run_relay(RelayDepsProvider(store, transport), now=lambda: _FIXED_NOW):
        await wait_until(lambda: msg.id in store.dispatched_ids)

    assert len(transport.sent) == 1
    assert msg.id in store.dispatched_ids
    assert msg.id not in store.discarded_ids


class TestRelayExpiration:
    @staticmethod
    async def test_expired_message_is_discarded_before_send() -> None:
        # A durable message whose delivery deadline elapsed while queued is DISCARDED at the relay,
        # never shipped to the broker (Wolverine DurableSendingAgent.SplitByExpiration parity).
        store = _RecordingOutboxStore()
        msg = _make_outbox_message(make_envelope(_TestEvent(value='stale'), expires_at=_PAST))
        store.pending.append(msg)
        transport = RecordingTransport()

        async with _run_relay(RelayDepsProvider(store, transport), now=lambda: _FIXED_NOW):
            await wait_until(lambda: msg.id in store.discarded_ids)

        assert transport.sent == []
        assert msg.id in store.discarded_ids
        assert msg.id not in store.dispatched_ids

    @staticmethod
    async def test_unexpired_message_is_sent() -> None:
        # Regression guard: a deadline still in the future must NOT be over-discarded.
        await _assert_relay_ships(make_envelope(_TestEvent(value='fresh'), expires_at=_FUTURE))

    @staticmethod
    async def test_message_with_no_expiry_is_sent() -> None:
        # The common case: no deadline set -> always sent.
        await _assert_relay_ships(make_envelope(_TestEvent(value='eternal')))
