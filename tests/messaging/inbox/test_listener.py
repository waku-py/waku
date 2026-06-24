from __future__ import annotations

import asyncio
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
from waku.messaging.inbox.backpressure import BufferingLimits, ListenerBackpressure
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.listener import InboundListener
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.registry import MessageRegistry
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import Subscription
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


def _receiver(container: AsyncContainer, executor: EndpointExecutor) -> DurableInboxReceiver:
    return DurableInboxReceiver(
        uri='local://test',
        container=container,
        executor=executor,
        inbox_owner_id='node-a:1',
        keep_after_handled=timedelta(seconds=300),
        max_requeue_attempts=5,
        max_buffer_size=100,
        stop_timeout=1.0,
    )


def _listener(container: AsyncContainer) -> tuple[InboundListener, DurableInboxReceiver]:
    serializer = make_serializer(_Event)
    registry = MessageRegistry()
    registry.handler_map.bind(_Event, _Handler)
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    receiver = _receiver(container, executor)
    return InboundListener(
        serializer=serializer,
        registry=registry,
        receiver=receiver,
    ), receiver


async def test_valid_message_persists_and_acks() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, receiver = _listener(container)
        serializer = make_serializer(_Event)
        envelope = make_envelope(_Event(kind='OrderPlaced'))
        body = serializer.serialize(envelope)

        await receiver.start()
        result = await listener.consume(body)
        await receiver.stop()

    assert result is ConsumeDisposition.ACK
    assert len(inbox.entries) == 1


async def test_redelivery_acks_without_double_process() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, receiver = _listener(container)
        serializer = make_serializer(_Event)
        envelope = make_envelope(_Event(kind='Shipped'))
        body = serializer.serialize(envelope)

        await receiver.start()
        first = await listener.consume(body)
        second = await listener.consume(body)
        await receiver.stop()

    assert first is ConsumeDisposition.ACK
    assert second is ConsumeDisposition.ACK
    assert len(inbox.entries) == 1


async def test_undeserializable_body_rejects() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, _ = _listener(container)
        result = await listener.consume({'garbage': 1})

    assert result is ConsumeDisposition.REJECT
    assert len(inbox.entries) == 0


async def test_unknown_type_with_no_handler_acks() -> None:
    @dataclass
    class _OtherEvent(IEvent):
        kind: str

    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        serializer_other = make_serializer(_Event, _OtherEvent)
        registry = MessageRegistry()
        registry.handler_map.bind(_Event, _Handler)
        executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
        listener = InboundListener(
            serializer=serializer_other,
            registry=registry,
            receiver=_receiver(container, executor),
        )

        envelope = make_envelope(_OtherEvent(kind='Unknown'))
        body = serializer_other.serialize(envelope)
        result = await listener.consume(body)

    assert result is ConsumeDisposition.ACK
    assert len(inbox.entries) == 0
    assert executor.calls == 0


async def test_transient_persist_failure_nacks_requeue() -> None:
    inbox = FakeInboxStore()
    inbox.store_incoming_error = RuntimeError('DB down')
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, _ = _listener(container)
        serializer = make_serializer(_Event)
        envelope = make_envelope(_Event(kind='Failed'))
        body = serializer.serialize(envelope)
        result = await listener.consume(body)

    assert result is ConsumeDisposition.NACK_REQUEUE
    assert len(inbox.entries) == 0


class _FakeSub(Subscription):
    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    async def pause(self) -> None:
        self.events.append('pause')

    @override
    async def resume(self) -> None:
        self.events.append('resume')


class _BlockingExecutor(EndpointExecutor):
    def __init__(self, *, release: asyncio.Event) -> None:
        self._release = release

    @override
    async def execute(self, envelope: object, handler_type: object, *, on_result: object = None) -> ExecutionResult:
        await self._release.wait()
        return ExecutionResult(outcome=ExecutionOutcome.SUCCESS, pause_duration=None)


async def test_consume_pauses_listener_when_buffer_crosses_high_watermark() -> None:
    inbox = FakeInboxStore()
    release = asyncio.Event()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        serializer = make_serializer(_Event)
        registry = MessageRegistry()
        registry.handler_map.bind(_Event, _Handler)
        receiver = DurableInboxReceiver(
            uri='local://test',
            container=container,
            executor=_BlockingExecutor(release=release),
            inbox_owner_id='node-a:1',
            keep_after_handled=timedelta(seconds=300),
            max_buffer_size=10,
            stop_timeout=1.0,
        )
        listener = InboundListener(serializer=serializer, registry=registry, receiver=receiver)
        sub = _FakeSub()
        listener.attach_backpressure(ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=1, low=0)))

        await receiver.start()
        # First item parks in the blocking handler; the second stays buffered → depth crosses high=1.
        await listener.consume(serializer.serialize(make_envelope(_Event(kind='a'))))
        await listener.consume(serializer.serialize(make_envelope(_Event(kind='b'))))
        await wait_until(lambda: sub.events == ['pause'])

        release.set()
        await receiver.stop()

    assert sub.events == ['pause']
