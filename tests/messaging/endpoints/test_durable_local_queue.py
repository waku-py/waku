from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.durable_local_queue import DurableLocalQueueEndpoint
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    make_envelope,
    make_serializer,
    wait_until,
)
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import IMessage
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
    def __init__(self, *, return_value: ExecutionOutcome) -> None:
        # Bypass parent __init__: tests don't exercise real dispatch.
        self.return_value = return_value
        self.calls = 0
        self.handled: list[HandlerType] = []

    @override
    async def execute(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> ExecutionOutcome:
        self.calls += 1
        self.handled.append(handler_type)
        return self.return_value


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
        self._serializer: IEnvelopeSerializer = make_serializer(_DomainEvent)
        self._uow: IUnitOfWork = FakeUoW()
        self._allocator = allocator or RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide
    def serializer(self) -> IEnvelopeSerializer:
        return self._serializer

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def sequence_allocator(self) -> ISequenceAllocator:
        return self._allocator


def _endpoint(
    container: Any,
    executor: _StubExecutor,
    handlers: frozenset[type[EventHandler[_DomainEvent]]],
    *,
    partition_by: PartitionKeyExtractor | None = None,
    inbox_owner_id: str = 'node-a:1',
) -> DurableLocalQueueEndpoint:
    return DurableLocalQueueEndpoint(
        uri='local://orders',
        handler_subscriptions={_DomainEvent: handlers},
        executor=executor,
        container=container,
        inbox_config_keep_after_handled_seconds=300.0,
        stop_timeout=1.0,
        max_buffer_size=math.inf,
        partition_by=partition_by,
        inbox_owner_id=inbox_owner_id,
    )


class TestDurableLocalQueueEndpoint:
    @staticmethod
    async def test_dispatch_persists_entry_before_enqueuing() -> None:
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
        # One message, two handlers on one durable endpoint: two inbox rows (one per handler FQN),
        # both handlers run, both rows become HANDLED.
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
        # Redelivery dedups every handler; after the HANDLED rows are purged (D4 retention window),
        # a fresh delivery re-runs both handlers.
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

                # Redelivery (same message_id): every (id, destination) row already exists ->
                # both handlers deduped synchronously in dispatch(), no extra execute.
                await endpoint.dispatch(envelope, scope)
                assert executor.calls == 2

                # D4: retention window elapses, cleanup purges the HANDLED rows.
                purged = await inbox.cleanup_handled(datetime.now(tz=UTC) + timedelta(minutes=10))
                assert purged == 2

                # A later delivery of the same message_id finds no conflict and re-runs both handlers.
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
            # NON-default owner id: a regression that drops the claim yields owner_id=None, not this value.
            endpoint = _endpoint(container, executor, frozenset([_NoopHandler]), inbox_owner_id='owner-claim-test')
            await endpoint.start()
            async with container() as scope:
                await endpoint.dispatch(make_envelope(_DomainEvent(kind='OrderPlaced')), scope)
            await endpoint.stop()  # drains the worker deterministically

        # owner_id is set ONLY at dispatch in this flow (mark_as_handled preserves it in FakeInboxStore).
        stored = next(iter(inbox.entries.values()))
        assert stored.owner_id == 'owner-claim-test'


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
        # Allocate ONCE per message: both per-handler rows carry the SAME sequence, and the allocator
        # is called exactly once — not once per handler. Envelope group_id wins over partition_by.
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
