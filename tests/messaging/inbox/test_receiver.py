from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.inbox.receiver import DurableReceiver
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    make_envelope,
    make_serializer,
    order_id_partition,
)
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.partition import PartitionKeyExtractor


@dataclass(frozen=True, kw_only=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        self.invocations.append(message.order_id)


class _FailingHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        msg = 'boom'
        raise RuntimeError(msg)


class _ReceiverDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        inbox: IInboxStore,
        dead_letter: IDeadLetterStore,
        allocator: ISequenceAllocator | None = None,
    ) -> None:
        super().__init__()
        self._inbox = inbox
        self._dead_letter = dead_letter
        self._serializer: IEnvelopeSerializer = make_serializer(_OrderPlaced)
        self._uow: IUnitOfWork = FakeUoW()
        self._allocator = allocator or RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dead_letter(self) -> IDeadLetterStore:
        return self._dead_letter

    @provide
    def serializer(self) -> IEnvelopeSerializer:
        return self._serializer

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def sequence_allocator(self) -> ISequenceAllocator:
        return self._allocator


class _StubExecutor(EndpointExecutor):
    def __init__(self, *, return_value: ExecutionOutcome) -> None:
        # Bypass parent __init__: tests don't exercise real dispatch.
        self.return_value = return_value
        self.invocations = 0

    @override
    async def execute(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> ExecutionOutcome:
        self.invocations += 1
        return self.return_value


def _receiver(
    container: Any,
    executor: _StubExecutor,
    *,
    partition_by: PartitionKeyExtractor | None = None,
) -> DurableReceiver:
    return DurableReceiver(
        container=container,
        executor=executor,
        inbox_config=InboxConfig(store=FakeInboxStore),
        owner_id='node-a:1',
        endpoint_uri='local://orders',
        partition_by=partition_by,
    )


class TestDurableReceiver:
    @staticmethod
    async def test_successful_handler_marks_entry_handled() -> None:
        _RecordingHandler.invocations = []
        inbox = FakeInboxStore()
        async with make_async_container(_ReceiverDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS))
            await receiver.receive(make_envelope(_OrderPlaced(order_id='o-1')), _RecordingHandler)

        stored = next(iter(inbox.entries.values()))
        assert stored.status is InboxStatus.HANDLED
        assert stored.keep_until is not None

    @staticmethod
    async def test_duplicate_message_is_discarded_silently() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_ReceiverDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_OrderPlaced(order_id='o-2'))
            await receiver.receive(envelope, _RecordingHandler)
            executor.return_value = ExecutionOutcome.DEAD_LETTERED  # would normally kick in
            await receiver.receive(envelope, _RecordingHandler)

        assert executor.invocations == 1

    @staticmethod
    async def test_dead_lettered_outcome_removes_inbox_entry() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_ReceiverDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(container, _StubExecutor(return_value=ExecutionOutcome.DEAD_LETTERED))
            await receiver.receive(make_envelope(_OrderPlaced(order_id='o-3')), _FailingHandler)

        # Composite-key: the handler's row was deleted, leaving the inbox empty.
        assert inbox.entries == {}

    @staticmethod
    async def test_discarded_outcome_removes_inbox_entry() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_ReceiverDepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(container, _StubExecutor(return_value=ExecutionOutcome.DISCARDED))
            await receiver.receive(make_envelope(_OrderPlaced(order_id='o-4')), _FailingHandler)

        # Composite-key: the handler's row was deleted, leaving the inbox empty.
        assert inbox.entries == {}


class TestDurableReceiverPartitioning:
    @staticmethod
    async def test_receive_persists_group_id_and_sequence_from_envelope() -> None:
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        async with make_async_container(
            _ReceiverDepsProvider(inbox, RecordingDeadLetterStore(), allocator)
        ) as container:
            receiver = _receiver(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS))
            await receiver.receive(make_envelope(_OrderPlaced(order_id='o-2'), group_id='o-2'), _RecordingHandler)

        stored = next(iter(inbox.entries.values()))
        assert stored.group_id == 'o-2'
        assert stored.sequence_number == 1
        assert allocator.calls == ['o-2']

    @staticmethod
    async def test_receive_applies_partition_by_when_envelope_has_no_group() -> None:
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        async with make_async_container(
            _ReceiverDepsProvider(inbox, RecordingDeadLetterStore(), allocator)
        ) as container:
            receiver = _receiver(
                container,
                _StubExecutor(return_value=ExecutionOutcome.SUCCESS),
                partition_by=order_id_partition,
            )
            await receiver.receive(make_envelope(_OrderPlaced(order_id='o-5')), _RecordingHandler)

        stored = next(iter(inbox.entries.values()))
        assert stored.group_id == 'o-5'
        assert stored.sequence_number == 1
        assert allocator.calls == ['o-5']
