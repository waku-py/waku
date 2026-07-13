from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import anyio.lowlevel
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.clock import utc_now
from waku.messages import IEvent
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.durability import IDeadLetterStore, IInboxStore
from waku.messaging.endpoints._internal.durable_local_queue import DurableLocalQueueEndpoint
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionResult
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_payload
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

from tests._wait import ControllableSleep, wait_until
from tests.messaging.helpers import (
    NOOP_OBSERVERS,
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingUoW,
    make_codec,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.messages import IMessage
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.partition import PartitionKeyExtractor


def _kind_partition(msg: IMessage) -> str | None:
    kind: str = msg.kind  # type: ignore[attr-defined]
    return kind


@dataclass(frozen=True, kw_only=True)
class _DomainEvent(IEvent):
    kind: str


class _NoopHandler(EventHandler[_DomainEvent]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _DomainEvent, /) -> None:
        self.invocations.append(message.kind)


class _SecondHandler(EventHandler[_DomainEvent]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _DomainEvent, /) -> None:
        self.invocations.append(message.kind)


class _StubExecutor(EndpointExecutor):
    def __init__(
        self,
        *,
        return_value: ExecutionOutcome,
        exc: Exception | None = None,
        pause_duration: timedelta | None = None,
    ) -> None:
        # Bypass parent __init__: tests don't exercise real dispatch.
        self.return_value = return_value
        self.exc = exc
        self._pause_duration = pause_duration
        self.calls = 0
        self.handled: list[HandlerType] = []

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        self.handled.append(handler_type)
        if on_result is not None:
            await on_result(self.return_value, self.exc)
        return ExecutionResult(self.return_value, self._pause_duration)


class _PauseOnceExecutor(EndpointExecutor):
    def __init__(self, *, pause_duration: timedelta) -> None:
        # PAUSED (with duration) on the first delivery, SUCCESS on the redelivery.
        self.calls = 0
        self._pause_duration = pause_duration

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        if self.calls == 1:
            result = ExecutionResult(ExecutionOutcome.PAUSED, self._pause_duration)
        else:
            result = ExecutionResult(ExecutionOutcome.SUCCESS)
        if on_result is not None:
            await on_result(result.outcome, None)
        return result


class _RequeueOnceExecutor(EndpointExecutor):
    def __init__(self) -> None:
        # Bypass parent __init__: returns REQUEUED on the first delivery, SUCCESS on the redelivery.
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        outcome = ExecutionOutcome.REQUEUED if self.calls == 1 else ExecutionOutcome.SUCCESS
        if on_result is not None:
            await on_result(outcome, None)
        return ExecutionResult(outcome)


class _EndpointDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        inbox: IInboxStore,
        dlq: IDeadLetterStore,
        allocator: ISequenceAllocator | None = None,
    ) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._codec = make_codec()
        self._uow: IUnitOfWork = RecordingUoW()
        self._allocator = allocator or RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def sequence_allocator(self) -> ISequenceAllocator:
        return self._allocator


def _endpoint(  # noqa: PLR0913 -- test helper mirroring DurableLocalQueueEndpoint's config surface
    container: AsyncContainer,
    executor: EndpointExecutor,
    handlers: frozenset[type[EventHandler[_DomainEvent]]],
    *,
    partition_by: PartitionKeyExtractor | None = None,
    inbox_owner_id: str = 'node-a:1',
    max_requeue_attempts: int = 5,
    pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    circuit_breaker_config: CircuitBreakerConfig | None = None,
    now: Callable[[], datetime] = utc_now,
    observers: MessageObservers = NOOP_OBSERVERS,
) -> DurableLocalQueueEndpoint:
    return DurableLocalQueueEndpoint(
        uri='local://orders',
        handler_subscriptions={_DomainEvent: handlers},
        executor=executor,
        observers=observers,
        container=container,
        inbox_config_keep_after_handled_seconds=300.0,
        stop_timeout=timedelta(seconds=1.0),
        max_buffer_size=math.inf,
        partition_by=partition_by,
        inbox_owner_id=inbox_owner_id,
        max_requeue_attempts=max_requeue_attempts,
        pause_sleep=pause_sleep,
        circuit_breaker_config=circuit_breaker_config,
        now=now,
    )


class TestDurableLocalQueueEndpoint:
    @staticmethod
    async def test_dispatch_persists_entry_and_marks_it_handled() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]))
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await endpoint.stop()  # stop() drains the worker deterministically

        assert len(inbox.entries) == 1
        assert next(iter(inbox.entries.values())).status is InboxStatus.HANDLED
        assert executor.calls == 1

    @staticmethod
    async def test_dispatch_drops_message_when_stopped() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]))
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='Ignored')), scope)

        # Dispatch before start silently drops (mirrors LocalQueueEndpoint).
        assert inbox.entries == {}
        assert executor.calls == 0

    @staticmethod
    async def test_duplicate_dispatch_is_deduplicated() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]))
            await endpoint.start()
            async with container() as scope:
                envelope = make_envelope(_DomainEvent(kind='OrderPlaced'))
                await endpoint.dispatch(envelope, scope)
                await endpoint.dispatch(envelope, scope)
            await endpoint.stop()

        assert executor.calls == 1

    @staticmethod
    async def test_fan_out_persists_and_handles_each_handler_independently() -> None:
        # One message, two handlers → two inbox rows (per-handler FQN), both HANDLED.
        _NoopHandler.invocations = []
        _SecondHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler, _SecondHandler]))
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await endpoint.stop()

        assert len(inbox.entries) == 2
        assert {entry.status for entry in inbox.entries.values()} == {InboxStatus.HANDLED}
        assert {dest for (_id, dest) in inbox.entries} == {
            f'{_NoopHandler.__module__}.{_NoopHandler.__qualname__}',
            f'{_SecondHandler.__module__}.{_SecondHandler.__qualname__}',
        }
        assert executor.calls == 2

    @staticmethod
    async def test_fan_out_redelivery_dedups_both_then_window_expiry_reruns() -> None:
        # Redelivery dedups; after HANDLED rows are purged (retention window), a fresh delivery re-runs both.
        inbox = FakeInboxStore()

        def handled_count() -> int:
            return sum(1 for entry in inbox.entries.values() if entry.status is InboxStatus.HANDLED)

        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler, _SecondHandler]))
            await endpoint.start()
            async with container() as scope:
                envelope = make_envelope(_DomainEvent(kind='OrderPlaced'))
                await endpoint.dispatch(envelope, scope)
                await wait_until(lambda: handled_count() == 2)

                # Redelivery: both rows already exist -> deduped in dispatch().
                await endpoint.dispatch(envelope, scope)
                assert executor.calls == 2

                # Retention window elapses, purging HANDLED rows.
                purged = await inbox.cleanup_handled(datetime.now(tz=UTC) + timedelta(minutes=10))
                assert purged == 2

                # Same message_id, no conflict -> re-runs both.
                await endpoint.dispatch(envelope, scope)
                await wait_until(lambda: executor.calls == 4)
            await endpoint.stop()

        assert executor.calls == 4

    @staticmethod
    async def test_dispatch_claims_rows_with_owner_id() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), inbox_owner_id='owner-claim-test')
            await endpoint.start()
            token = await endpoint.pause()  # gate processing so the claim is observed before mark_as_handled clears it
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)

            stored = next(iter(inbox.entries.values()))
            assert stored.status is InboxStatus.INCOMING
            assert stored.owner_id == 'owner-claim-test'  # dispatch claimed the row

            await endpoint.resume(token)
            await endpoint.stop()

    @staticmethod
    async def test_pause_blocks_processing_until_resume() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]))
            await endpoint.start()
            token = await endpoint.pause()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            # Pause BEFORE dispatch: item enters the stream already gated, worker blocks before
            # _process_envelope. Give it loop turns to (wrongly) advance — it must not.
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert executor.calls == 0
            assert len(inbox.entries) == 1  # dispatch persisted; only processing is gated
            await endpoint.resume(token)
            await wait_until(lambda: executor.calls == 1)
            await endpoint.stop()

        assert executor.calls == 1


class TestDurableLocalQueueInboxDecomposition:
    @staticmethod
    async def test_persist_writes_decomposed_inbox_row() -> None:
        # persist() must store encoded payload + metadata + typed correlation/causation columns.
        inbox = FakeInboxStore()
        codec = make_codec()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]))
            await endpoint.start()
            envelope = make_envelope(_DomainEvent(kind='OrderPlaced'))
            token = await endpoint.pause()
            async with container() as scope:
                await endpoint.dispatch(envelope, scope)
            await endpoint.resume(token)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.correlation_id == envelope.correlation_id
        assert entry.causation_id == envelope.causation_id
        assert entry.metadata is not None
        assert 'timestamp' in entry.metadata
        assert 'correlation_id' not in entry.payload
        assert 'causation_id' not in entry.payload
        assert entry.payload == encode_payload(envelope, codec)

    @staticmethod
    async def test_store_scheduled_writes_decomposed_inbox_row() -> None:
        # _store_scheduled() must store encoded payload + metadata + typed correlation/causation columns.
        NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
        inbox = FakeInboxStore()
        codec = make_codec()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                now=lambda: NOW,
            )
            await endpoint.start()
            scheduled = NOW + timedelta(hours=1)
            envelope = make_envelope(_DomainEvent(kind='later'), scheduled_time=scheduled)
            async with container() as scope:
                await endpoint.dispatch(envelope, scope)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.status is InboxStatus.SCHEDULED
        assert entry.correlation_id == envelope.correlation_id
        assert entry.causation_id == envelope.causation_id
        assert entry.metadata is not None
        assert 'timestamp' in entry.metadata
        assert 'correlation_id' not in entry.payload
        assert 'causation_id' not in entry.payload
        assert entry.payload == encode_payload(envelope, codec)


class TestDurableLocalQueuePartitioning:
    @staticmethod
    async def test_partition_by_callable_persists_group_id_and_sequence() -> None:
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        async with make_async_container(
            _EndpointDepsProvider(inbox, RecordingDeadLetterStore(), allocator)
        ) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), partition_by=_kind_partition)
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='shipments')), scope)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.group_id == 'shipments'
        assert entry.sequence_number == 1
        assert allocator.calls == ['shipments']

    @staticmethod
    async def test_fan_out_handlers_share_one_allocated_sequence() -> None:
        # Both per-handler rows carry the same sequence; allocator called once, not per handler.
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        async with make_async_container(
            _EndpointDepsProvider(inbox, RecordingDeadLetterStore(), allocator)
        ) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler, _SecondHandler]))
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced'), group_id='order-1'), scope)
            await endpoint.stop()

        assert len(inbox.entries) == 2
        assert {e.group_id for e in inbox.entries.values()} == {'order-1'}
        assert {e.sequence_number for e in inbox.entries.values()} == {1}
        assert allocator.calls == ['order-1']


class TestDurableLocalQueueScheduled:
    _NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)

    @staticmethod
    async def test_future_scheduled_message_persists_scheduled_row_without_enqueueing() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                now=lambda: TestDurableLocalQueueScheduled._NOW,
            )
            await endpoint.start()
            scheduled = TestDurableLocalQueueScheduled._NOW + timedelta(hours=1)
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='later'), scheduled_time=scheduled), scope)
            for _ in range(10):
                await anyio.lowlevel.checkpoint()  # give a (wrongly) enqueued item turns to run — it must not
            await endpoint.stop()

        assert len(inbox.entries) == 1
        entry = next(iter(inbox.entries.values()))
        assert entry.status is InboxStatus.SCHEDULED
        assert entry.execution_time == scheduled
        assert entry.sequence_number is None
        assert executor.calls == 0  # SCHEDULED rows are NOT enqueued

    @staticmethod
    async def test_keyed_future_scheduled_message_resolves_group_without_allocating() -> None:
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        async with make_async_container(
            _EndpointDepsProvider(inbox, RecordingDeadLetterStore(), allocator)
        ) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                partition_by=_kind_partition,
                now=lambda: TestDurableLocalQueueScheduled._NOW,
            )
            await endpoint.start()
            scheduled = TestDurableLocalQueueScheduled._NOW + timedelta(hours=1)
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='shipments'), scheduled_time=scheduled), scope)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.group_id == 'shipments'  # partition resolved at dispatch
        assert entry.sequence_number is None  # but allocation deferred to promotion (BLOCKER 1)
        assert allocator.calls == []

    @staticmethod
    async def test_past_scheduled_time_dispatches_immediately() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                now=lambda: TestDurableLocalQueueScheduled._NOW,
            )
            await endpoint.start()
            past = TestDurableLocalQueueScheduled._NOW - timedelta(hours=1)
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='due'), scheduled_time=past), scope)
            await wait_until(lambda: executor.calls == 1)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.status is InboxStatus.HANDLED  # already due → immediate INCOMING path, not SCHEDULED


class TestDurableLocalQueueCircuitBreaker:
    @staticmethod
    async def test_circuit_breaker_trips_and_pauses_processing() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.FAILED_NO_POLICY, exc=RuntimeError())
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                circuit_breaker_config=CircuitBreakerConfig(
                    minimum_throughput=2,
                    failure_rate_threshold=0.5,
                    pause_time=timedelta(minutes=5),  # large: the timed resume must NOT fire during the test
                ),
            )
            await endpoint.start()
            try:
                async with container() as scope:
                    for _ in range(4):
                        await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderFailed')), scope)
                # After 2 failures the breaker trips → worker halts.
                await wait_until(lambda: executor.calls >= 2)
                # Remaining messages stay enqueued (if CB were absent, all 4 would run).
                for _ in range(10):
                    await anyio.lowlevel.checkpoint()
                assert executor.calls == 2
            finally:
                await endpoint.stop()  # aclose()s the CB, cancelling the parked resume (no real time elapsed)


class TestDurableLocalQueueRequeue:
    @staticmethod
    async def test_requeue_increments_attempts_and_reprocesses() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _RequeueOnceExecutor()
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), max_requeue_attempts=5)
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await wait_until(lambda: executor.calls >= 2)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.attempts == 1  # incremented once on the single requeue
        assert entry.status is InboxStatus.HANDLED  # redelivery succeeded

    @staticmethod
    async def test_requeue_dead_letters_at_bound() -> None:
        inbox = FakeInboxStore()
        dlq = RecordingDeadLetterStore()
        async with make_async_container(_EndpointDepsProvider(inbox, dlq)) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), max_requeue_attempts=2)
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='Poison')), scope)
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)
            await endpoint.stop()

        assert inbox.entries == {}  # the row was moved to the dead-letter table
        assert len(inbox.dead_letters.entries) == 1


class TestDurableLocalQueuePause:
    @staticmethod
    async def test_pause_policy_pauses_then_resumes_and_reprocesses() -> None:
        inbox = FakeInboxStore()
        sleep = ControllableSleep()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _PauseOnceExecutor(pause_duration=timedelta(minutes=10))
            endpoint = _endpoint(
                container, executor, frozenset([_NoopHandler]), max_requeue_attempts=5, pause_sleep=sleep
            )
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await wait_until(lambda: sleep.requested == [600.0])  # paused 10min after the first failure
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert executor.calls == 1  # the re-enqueued delivery is gated while paused
            sleep.released.set()  # auto-resume
            await wait_until(lambda: executor.calls == 2)
            await endpoint.stop()

        entry = next(iter(inbox.entries.values()))
        assert entry.status is InboxStatus.HANDLED

    @staticmethod
    async def test_pause_dead_letters_at_shared_budget() -> None:
        inbox = FakeInboxStore()
        sleep = ControllableSleep()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.PAUSED, pause_duration=timedelta(minutes=10))
            endpoint = _endpoint(
                container, executor, frozenset([_NoopHandler]), max_requeue_attempts=2, pause_sleep=sleep
            )
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='Poison')), scope)
            await wait_until(lambda: sleep.requested == [600.0])  # paused once after the first PAUSED
            sleep.released.set()  # resume -> the second PAUSED hits the shared budget -> DLQ, not a third pause
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)
            await endpoint.stop()

        assert len(sleep.requested) == 1  # the budget-exhausted delivery dead-letters instead of pausing again
        assert inbox.entries == {}

    @staticmethod
    async def test_pause_action_and_breaker_hold_coexist_without_premature_resume() -> None:
        # PAUSE action's hold and CB hold share one refcounted gate; the PAUSE timer firing must NOT
        # resume the listener while the CB hold is still down.
        inbox = FakeInboxStore()
        sleep = ControllableSleep()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _PauseOnceExecutor(pause_duration=timedelta(minutes=10))
            endpoint = _endpoint(
                container, executor, frozenset([_NoopHandler]), max_requeue_attempts=5, pause_sleep=sleep
            )
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await wait_until(lambda: sleep.requested == [600.0])  # handler PAUSEd -> a TimedPauser token is held
            breaker_token = await endpoint.pause()  # a second hold via the breaker's exact callback
            sleep.released.set()  # the PAUSE timer fires and releases ITS token...
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert executor.calls == 1  # ...but the breaker-style hold keeps the listener paused -> no redelivery
            await endpoint.resume(breaker_token)  # release the remaining hold
            await wait_until(lambda: executor.calls == 2)  # only now does the redelivery run
            await endpoint.stop()


class _SentSpy(IMessageObserver):
    def __init__(self) -> None:
        self.sent: list[str] = []

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self.sent.append(destination)


class TestDurableLocalQueueOnSent:
    @staticmethod
    async def test_dispatch_fires_on_sent_after_fresh_immediate_enqueue() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        spy = _SentSpy()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), observers=MessageObservers([spy]))
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await endpoint.stop()

        assert spy.sent == ['local://orders']

    @staticmethod
    async def test_dispatch_fires_on_sent_for_scheduled_message() -> None:
        now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
        inbox = FakeInboxStore()
        spy = _SentSpy()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(
                container,
                executor,
                frozenset([_NoopHandler]),
                now=lambda: now,
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            scheduled = now + timedelta(hours=1)
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='later'), scheduled_time=scheduled), scope)
            await endpoint.stop()

        assert spy.sent == ['local://orders']

    @staticmethod
    async def test_duplicate_dispatch_does_not_refire_on_sent() -> None:
        _NoopHandler.invocations = []
        inbox = FakeInboxStore()
        spy = _SentSpy()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), observers=MessageObservers([spy]))
            await endpoint.start()
            async with container() as scope:
                envelope = make_envelope(_DomainEvent(kind='OrderPlaced'))
                await endpoint.dispatch(envelope, scope)
                await endpoint.dispatch(envelope, scope)
            await endpoint.stop()

        assert spy.sent == ['local://orders']  # the duplicate (not fresh) dispatch does not re-fire

    @staticmethod
    async def test_dispatch_to_stopped_endpoint_does_not_fire_on_sent() -> None:
        inbox = FakeInboxStore()
        spy = _SentSpy()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), observers=MessageObservers([spy]))
            # Endpoint never started -> the receiver rejects the dispatch, mirroring the BUFFERED endpoint.
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)

        assert spy.sent == []

    @staticmethod
    async def test_dispatch_with_no_subscribed_handlers_does_not_fire_on_sent() -> None:
        inbox = FakeInboxStore()
        spy = _SentSpy()
        async with make_async_container(_EndpointDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            endpoint = _endpoint(container, executor, frozenset(), observers=MessageObservers([spy]))
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await endpoint.stop()

        assert spy.sent == []
