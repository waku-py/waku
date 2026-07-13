from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.retort import default_retort
from waku.messages import IEvent
from waku.messaging.durability import IOutboxStore
from waku.messaging.endpoints._internal.external import ExternalEndpoint
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec

from tests.messaging.helpers import NOOP_OBSERVERS, RecordingAllocator, make_envelope, order_id_partition
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


def _make_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


class _TestDepsProvider(Provider):
    scope = Scope.APP

    def __init__(
        self,
        outbox: RecordingOutboxStore,
        codec: PayloadCodec,
        allocator: ISequenceAllocator | None = None,
    ) -> None:
        super().__init__()
        self._outbox = outbox
        self._codec = codec
        self._allocator = allocator or RecordingAllocator()

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._outbox

    @provide
    def payload_codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def sequence_allocator(self) -> ISequenceAllocator:
        return self._allocator


class TestExternalEndpoint:
    @staticmethod
    async def test_dispatch_persists_decomposed_message_to_outbox() -> None:
        outbox = RecordingOutboxStore()
        codec = _make_codec()

        async with make_async_container(_TestDepsProvider(outbox, codec)) as container:
            endpoint = ExternalEndpoint(uri='notifications', observers=NOOP_OBSERVERS)
            envelope = make_envelope(_OrderPlaced(order_id='123'), headers={'tenant': 'acme'})

            await endpoint.dispatch(envelope, container)

        assert len(outbox.saved) == 1
        msg = outbox.saved[0]
        assert msg.destination == 'notifications'
        assert msg.message_type == envelope.message_type
        assert msg.correlation_id == envelope.correlation_id
        assert msg.causation_id == envelope.causation_id
        assert msg.idempotency_key == str(envelope.message_id)
        assert msg.payload == encode_payload(envelope, codec)
        assert msg.metadata == encode_metadata(envelope)

    @staticmethod
    async def test_start_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test', observers=NOOP_OBSERVERS)
        await endpoint.start()

    @staticmethod
    async def test_stop_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test', observers=NOOP_OBSERVERS)
        await endpoint.stop()

    @staticmethod
    def test_supports_scheduling_is_false() -> None:
        endpoint = ExternalEndpoint(uri='rabbitmq://orders', observers=NOOP_OBSERVERS)
        assert endpoint.supports_scheduling is False


class _SentSpy(IMessageObserver):
    def __init__(self) -> None:
        self.sent: list[str] = []

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self.sent.append(destination)


class TestExternalEndpointOnSent:
    @staticmethod
    async def test_dispatch_fires_on_sent_after_outbox_write() -> None:
        outbox = RecordingOutboxStore()
        codec = _make_codec()
        spy = _SentSpy()
        async with make_async_container(_TestDepsProvider(outbox, codec)) as container:
            endpoint = ExternalEndpoint(uri='notifications', observers=MessageObservers([spy]))
            envelope = make_envelope(_OrderPlaced(order_id='123'))

            await endpoint.dispatch(envelope, container)

        assert len(outbox.saved) == 1  # the write happened before on_sent fired
        assert spy.sent == ['notifications']


class TestExternalEndpointPartitioning:
    @staticmethod
    async def test_envelope_group_id_wins_over_partition_by() -> None:
        outbox = RecordingOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(
                uri='test://out', partition_by=lambda _msg: 'from-callable', observers=NOOP_OBSERVERS
            )
            envelope = make_envelope(_OrderPlaced(order_id='o-1'), group_id='from-envelope')

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id == 'from-envelope'
        assert outbox.saved[0].sequence_number == 1
        assert allocator.calls == ['from-envelope']

    @staticmethod
    async def test_falls_back_to_partition_by_when_no_envelope_group_id() -> None:
        outbox = RecordingOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(uri='test://out', partition_by=order_id_partition, observers=NOOP_OBSERVERS)
            envelope = make_envelope(_OrderPlaced(order_id='o-7'))

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id == 'o-7'
        assert outbox.saved[0].sequence_number == 1
        assert allocator.calls == ['o-7']

    @staticmethod
    async def test_keyless_message_skips_sequence_allocation() -> None:
        outbox = RecordingOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(uri='test://out', partition_by=None, observers=NOOP_OBSERVERS)
            envelope = make_envelope(_OrderPlaced(order_id='o-11'))

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id is None
        assert outbox.saved[0].sequence_number is None
        assert allocator.calls == []
