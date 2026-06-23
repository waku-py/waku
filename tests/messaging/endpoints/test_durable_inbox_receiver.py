from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome, ExecutionResult
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    make_envelope,
    make_serializer,
)
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass
class _Event(IEvent):
    kind: str


class _Handler(EventHandler[_Event]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _Event, /) -> None:
        self.invocations.append(message.kind)


class _DepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore, dlq: IDeadLetterStore) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._serializer: IEnvelopeSerializer = make_serializer(_Event)
        self._uow: IUnitOfWork = FakeUoW()
        self._allocator: ISequenceAllocator = RecordingAllocator()

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
    def allocator(self) -> ISequenceAllocator:
        return self._allocator


class _StubExecutor(EndpointExecutor):
    def __init__(self, *, return_value: ExecutionOutcome) -> None:
        # Bypass parent __init__: tests don't exercise real dispatch.
        self.return_value = return_value
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: object,
        handler_type: object,
        *,
        on_result: object = None,
    ) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(outcome=self.return_value, pause_duration=None)


def _receiver(
    container: AsyncContainer,
    executor: EndpointExecutor,
    *,
    max_requeue_attempts: int = 5,
    max_buffer_size: float = 100,
    stop_timeout: float = 1.0,
) -> DurableInboxReceiver:
    return DurableInboxReceiver(
        uri='local://test',
        container=container,
        executor=executor,
        inbox_owner_id='node-a:1',
        keep_after_handled=timedelta(seconds=300),
        max_requeue_attempts=max_requeue_attempts,
        max_buffer_size=max_buffer_size,
        stop_timeout=stop_timeout,
    )


class TestDurableInboxReceiverPersist:
    @staticmethod
    async def test_persist_returns_only_fresh_handlers() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == handler_types

    @staticmethod
    async def test_persist_re_persisting_same_id_and_handler_returns_empty() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            await receiver.persist(envelope, handler_types)
            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == frozenset()


class TestDurableInboxReceiverProcess:
    @staticmethod
    async def test_enqueue_and_success_marks_inbox_row_handled() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Shipped'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await receiver.stop()

        assert executor.calls == 1
        rows = list(inbox.entries.values())
        assert len(rows) == 1
        assert rows[0].status is InboxStatus.HANDLED

    @staticmethod
    async def test_requeue_increments_attempts_and_reprocesses() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            call_count = 0

            class _RequeueOnceThenSucceed(_StubExecutor):
                @override
                async def execute(
                    self,
                    envelope: object,
                    handler_type: object,
                    *,
                    on_result: object = None,
                ) -> ExecutionResult:
                    nonlocal call_count
                    call_count += 1
                    outcome = ExecutionOutcome.REQUEUED if call_count == 1 else ExecutionOutcome.SUCCESS
                    return ExecutionResult(outcome=outcome, pause_duration=None)

            executor = _RequeueOnceThenSucceed(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Billed'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: call_count >= 2)
            await receiver.stop()

        assert call_count == 2  # requeued once then succeeded
        row = next(iter(inbox.entries.values()))
        assert row.status is InboxStatus.HANDLED

    @staticmethod
    async def test_exceeding_max_requeue_attempts_moves_to_dead_letter() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED)
            receiver = _receiver(container, executor, max_requeue_attempts=3, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='Poison'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_lettered) == 1)
            await receiver.stop()

        assert len(inbox.dead_lettered) == 1
