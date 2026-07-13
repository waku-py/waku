from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.retort import default_retort
from waku.messages import IEvent
from waku.messaging import HandlerMap
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.durability import IDeadLetterStore, IInboxStore
from waku.messaging.endpoints._internal.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionResult
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.handler import EventHandler
from waku.messaging.inbox._internal.listener import InboundListener
from waku.messaging.inbox.backpressure import BufferingLimits, ListenerBackpressure
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import EnvelopeMetadata, Subscription
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    make_envelope,
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


def _make_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


def _make_type_registry(*types: type[IEvent]) -> MessageTypeRegistry:
    return MessageTypeRegistry(identities={}, known_types=list(types))


class _DepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore, dlq: IDeadLetterStore) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._uow: IUnitOfWork = FakeUoW()
        self._allocator: ISequenceAllocator = RecordingAllocator()
        self._codec = _make_codec()

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
        stop_timeout=timedelta(seconds=1.0),
    )


def _listener(container: AsyncContainer) -> tuple[InboundListener, DurableInboxReceiver]:
    codec = _make_codec()
    type_registry = _make_type_registry(_Event)
    registry = HandlerMap()
    registry.bind(_Event, _Handler)
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    receiver = _receiver(container, executor)
    return InboundListener(
        codec=codec,
        type_registry=type_registry,
        handler_map=registry,
        receiver=receiver,
    ), receiver


async def test_valid_message_persists_and_acks() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, receiver = _listener(container)
        codec = _make_codec()
        envelope = make_envelope(_Event(kind='OrderPlaced'))
        payload, metadata = encode_payload(envelope, codec), envelope_metadata_of(envelope)

        await receiver.start()
        result = await listener.consume(payload, metadata)
        await receiver.stop()

    assert result is ConsumeDisposition.ACK
    assert len(inbox.entries) == 1
    # P2 decomposition: listener must populate the typed columns from envelope metadata.
    stored = next(iter(inbox.entries.values()))
    assert stored.correlation_id == envelope.correlation_id
    assert stored.causation_id == envelope.causation_id
    assert stored.metadata is not None
    assert stored.metadata.get('message_version') == envelope.message_version
    assert 'headers' in stored.metadata


async def test_redelivery_acks_without_double_process() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, receiver = _listener(container)
        codec = _make_codec()
        envelope = make_envelope(_Event(kind='Shipped'))
        payload, metadata = encode_payload(envelope, codec), envelope_metadata_of(envelope)

        await receiver.start()
        first = await listener.consume(payload, metadata)
        second = await listener.consume(payload, metadata)
        await receiver.stop()

    assert first is ConsumeDisposition.ACK
    assert second is ConsumeDisposition.ACK
    assert len(inbox.entries) == 1


async def test_foreign_message_type_rejects() -> None:
    # A foreign/unknown message_type in metadata (no registered type) → REJECT, no crash.
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, _ = _listener(container)
        # Use a real payload shape but a type name that is not in the type_registry.
        foreign_metadata = EnvelopeMetadata(
            message_id='11111111-1111-1111-1111-111111111111',
            correlation_id='22222222-2222-2222-2222-222222222222',
            causation_id='33333333-3333-3333-3333-333333333333',
            message_type='some.unknown.ForeignEvent',
            timestamp=datetime.now(tz=UTC),
        )
        result = await listener.consume({'kind': 'ping'}, foreign_metadata)

    assert result is ConsumeDisposition.REJECT
    assert len(inbox.entries) == 0


async def test_foreign_non_uuid_correlation_survives_inbound_persist() -> None:
    # A registered type carrying a foreign non-UUID correlation id (e.g. an upstream trace id) must
    # rebuild and persist verbatim — no ValueError, no quarantine.
    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, receiver = _listener(container)
        codec = _make_codec()
        envelope = make_envelope(_Event(kind='OrderPlaced'))
        payload = encode_payload(envelope, codec)
        metadata = replace(envelope_metadata_of(envelope), correlation_id='trace-abc-123')

        await receiver.start()
        result = await listener.consume(payload, metadata)
        await receiver.stop()

    assert result is ConsumeDisposition.ACK
    stored = next(iter(inbox.entries.values()))
    assert stored.correlation_id == 'trace-abc-123'


async def test_unknown_type_with_no_handler_acks() -> None:
    @dataclass
    class _OtherEvent(IEvent):
        kind: str

    inbox = FakeInboxStore()
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        codec = _make_codec()
        # type_registry knows both types; handler registry only has _Event
        type_registry = _make_type_registry(_Event, _OtherEvent)
        registry = HandlerMap()
        registry.bind(_Event, _Handler)
        executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
        listener = InboundListener(
            codec=codec,
            type_registry=type_registry,
            handler_map=registry,
            receiver=_receiver(container, executor),
        )

        envelope = make_envelope(_OtherEvent(kind='Unknown'))
        payload, metadata = encode_payload(envelope, codec), envelope_metadata_of(envelope)
        result = await listener.consume(payload, metadata)

    assert result is ConsumeDisposition.ACK
    assert len(inbox.entries) == 0
    assert executor.calls == 0


async def test_transient_persist_failure_nacks_requeue() -> None:
    inbox = FakeInboxStore()
    inbox.store_incoming_error = RuntimeError('DB down')
    async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
        listener, _ = _listener(container)
        codec = _make_codec()
        envelope = make_envelope(_Event(kind='Failed'))
        payload, metadata = encode_payload(envelope, codec), envelope_metadata_of(envelope)
        result = await listener.consume(payload, metadata)

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
        codec = _make_codec()
        type_registry = _make_type_registry(_Event)
        registry = HandlerMap()
        registry.bind(_Event, _Handler)
        receiver = DurableInboxReceiver(
            uri='local://test',
            container=container,
            executor=_BlockingExecutor(release=release),
            inbox_owner_id='node-a:1',
            keep_after_handled=timedelta(seconds=300),
            max_buffer_size=10,
            stop_timeout=timedelta(seconds=1.0),
        )
        listener = InboundListener(codec=codec, type_registry=type_registry, handler_map=registry, receiver=receiver)
        sub = _FakeSub()
        listener.attach_backpressure(ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=1, low=0)))

        await receiver.start()
        # First item parks in the blocking handler; the second stays buffered → depth crosses high=1.
        env_a = make_envelope(_Event(kind='a'))
        env_b = make_envelope(_Event(kind='b'))
        await listener.consume(encode_payload(env_a, codec), envelope_metadata_of(env_a))
        await listener.consume(encode_payload(env_b, codec), envelope_metadata_of(env_b))
        await wait_until(lambda: sub.events == ['pause'])

        release.set()
        await receiver.stop()

    assert sub.events == ['pause']
