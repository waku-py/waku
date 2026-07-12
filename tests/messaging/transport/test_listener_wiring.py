from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.di import object_, scoped, singleton
from waku.messages import IEvent
from waku.messaging import (
    IMessageObserver,
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    TransactionalBehavior,
)
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.endpoints.base import BrokerEndpointEntry
from waku.messaging.handler import EventHandler
from waku.messaging.router import external_endpoint, listen
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingTransport, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from datetime import timedelta

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.outcome import ExecutionOutcome


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _EndpointSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


class _EndpointOnlyObserver(IMessageObserver):
    def __init__(self, sink: _EndpointSink) -> None:
        self._sink = sink

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self._sink.events.append(('executing', destination))

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._sink.events.append(('executed', destination))


# Observable identity mapper — only referenced for identity comparison; never invoked.
class _MarkerMapper(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> Any:
        raise NotImplementedError  # pragma: no cover

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raise NotImplementedError  # pragma: no cover


def _config(transport: RecordingTransport, *, entry: BrokerEndpointEntry) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[entry],
        inbox=InboxConfig(owner_id='test-node:1'),
        transports={'test': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class TestListenerMapperOverrideWiring:
    @staticmethod
    async def test_per_route_mapper_override_reaches_subscribe() -> None:
        # The critical end-to-end wiring proof:
        # BrokerEndpointEntry.mapper → merge_broker_endpoints → registry.mapper_for
        # → TransportLifecycleExtension → ListeningAgent.start() → RecordingTransport.subscribe(mapper=override).
        # Observable via the 3rd element of the recording tuple — not mock internals.
        override_mapper = _MarkerMapper()
        transport = RecordingTransport()
        FakeInboxStore()
        config = _config(transport, entry=listen('test://orders', mapper=override_mapper))

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                scoped(IOutboxStore, InMemoryOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
            ],
        ):
            pass

        assert len(transport.subscribed) == 1
        _queue, _on_message, mapper = transport.subscribed[0]
        # The override mapper instance must be exactly the one configured — proves it flowed through
        # registry.mapper_for (the single projection), not a per-entry field read directly off the endpoint.
        assert mapper is override_mapper

    @staticmethod
    async def test_no_override_configured_subscribes_with_none_mapper() -> None:
        # No BrokerEndpointEntry.mapper configured → registry.mapper_for returns None
        # → subscribe(mapper=None).
        transport = RecordingTransport()
        FakeInboxStore()
        config = _config(transport, entry=listen('test://orders'))

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                scoped(IOutboxStore, InMemoryOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
            ],
        ):
            pass

        assert len(transport.subscribed) == 1
        _queue, _on_message, mapper = transport.subscribed[0]
        assert mapper is None


class TestBidirectionalEndpointMapperInheritance:
    @staticmethod
    async def test_send_declared_mapper_reaches_listener_subscribe_on_bidirectional_uri() -> None:
        # Same URI declared as two fragments: external_endpoint (send, carries the mapper) + listen
        # (no mapper). merge_broker_endpoints combines them into one MergedBrokerEndpoint whose mapper
        # comes from the send fragment; ListeningAgent.start() then reads it via registry.mapper_for and
        # passes it to subscribe. In the old parallel inbound/outbound model the listen side had its
        # own independent mapper field, which would be None here — this discriminates that.
        send_mapper = _MarkerMapper()
        transport = RecordingTransport()
        config = MessagingConfig(
            endpoints=[
                external_endpoint('test://orders', mapper=send_mapper),
                listen('test://orders'),
            ],
            outbox=OutboxConfig(),
            inbox=InboxConfig(owner_id='test-node:1'),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                scoped(IOutboxStore, InMemoryOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
            ],
        ):
            pass

        assert len(transport.subscribed) == 1
        _queue, _on_message, mapper = transport.subscribed[0]
        # The mapper declared ONLY on the send fragment is exactly the instance reaching the
        # listener's subscribe — proves per-URI sharing across directions, not per-aspect isolation.
        assert mapper is send_mapper


class TestListenerObserverWiring:
    @staticmethod
    async def test_listen_declared_observer_fires_on_inbound_consume() -> None:
        class _RecordingHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None: ...

        transport = RecordingTransport()
        inbox = FakeInboxStore()
        config = _config(transport, entry=listen('test://orders', observers=(_EndpointOnlyObserver,)))

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                singleton(_EndpointSink),
            ],
        ) as app:
            sink = await app.container.get(_EndpointSink)
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='wired-1'))
            _queue, on_message, _mapper = transport.subscribed[0]
            await on_message(encode_payload(envelope, codec), envelope_metadata_of(envelope))
            await wait_until(lambda: sink.events == [('executing', 'test://orders'), ('executed', 'test://orders')])

        assert sink.events == [('executing', 'test://orders'), ('executed', 'test://orders')]
