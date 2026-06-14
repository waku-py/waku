from __future__ import annotations

from dataclasses import dataclass

from dishka import Provider, Scope, make_async_container, provide

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.external import ExternalEndpoint
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.transport.serialization import IEnvelopeSerializer

from tests.messaging.helpers import make_envelope, make_serializer
from tests.messaging.outbox.fake_store import FakeOutboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _TestDepsProvider(Provider):
    scope = Scope.APP

    def __init__(self, outbox: FakeOutboxStore, serializer: IEnvelopeSerializer) -> None:
        super().__init__()
        self._outbox = outbox
        self._serializer = serializer

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._outbox

    @provide
    def envelope_serializer(self) -> IEnvelopeSerializer:
        return self._serializer


class TestExternalEndpoint:
    @staticmethod
    async def test_dispatch_persists_serialized_message_to_outbox() -> None:
        outbox = FakeOutboxStore()
        serializer = make_serializer(_OrderPlaced)

        async with make_async_container(_TestDepsProvider(outbox, serializer)) as container:
            endpoint = ExternalEndpoint(uri='notifications')
            envelope = make_envelope(_OrderPlaced(order_id='123'), headers={'tenant': 'acme'})

            await endpoint.dispatch(envelope, container)

        assert len(outbox.saved) == 1
        msg = outbox.saved[0]
        assert msg.destination == 'notifications'
        assert msg.message_type == envelope.message_type
        assert msg.correlation_id == envelope.correlation_id
        assert msg.causation_id == envelope.causation_id
        assert msg.payload == serializer.serialize(envelope)
        assert msg.idempotency_key == str(envelope.message_id)

    @staticmethod
    async def test_start_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test')
        await endpoint.start()

    @staticmethod
    async def test_stop_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test')
        await endpoint.stop()
