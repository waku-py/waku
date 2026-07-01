from __future__ import annotations

from dataclasses import dataclass

from dishka import Provider, Scope, make_async_container, provide

from waku._internal.retort import default_retort  # noqa: PLC2701
from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.external import ExternalEndpoint
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport.decomposition import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec
from waku.serialization.upcasting import UpcasterChain

from tests.messaging.helpers import RecordingAllocator, make_envelope, order_id_partition
from tests.messaging.outbox.fake_store import FakeOutboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


def _make_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


class _TestDepsProvider(Provider):
    scope = Scope.APP

    def __init__(
        self,
        outbox: FakeOutboxStore,
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
        outbox = FakeOutboxStore()
        codec = _make_codec()

        async with make_async_container(_TestDepsProvider(outbox, codec)) as container:
            endpoint = ExternalEndpoint(uri='notifications')
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
        assert msg.metadata_ == encode_metadata(envelope)

    @staticmethod
    async def test_start_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test')
        await endpoint.start()

    @staticmethod
    async def test_stop_is_noop() -> None:
        endpoint = ExternalEndpoint(uri='test')
        await endpoint.stop()

    @staticmethod
    def test_supports_scheduling_is_false() -> None:
        endpoint = ExternalEndpoint(uri='rabbitmq://orders')
        assert endpoint.supports_scheduling is False


class TestExternalEndpointPartitioning:
    @staticmethod
    async def test_envelope_group_id_wins_over_partition_by() -> None:
        outbox = FakeOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(uri='test://out', partition_by=lambda _msg: 'from-callable')
            envelope = make_envelope(_OrderPlaced(order_id='o-1'), group_id='from-envelope')

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id == 'from-envelope'
        assert outbox.saved[0].sequence_number == 1
        assert allocator.calls == ['from-envelope']

    @staticmethod
    async def test_falls_back_to_partition_by_when_no_envelope_group_id() -> None:
        outbox = FakeOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(uri='test://out', partition_by=order_id_partition)
            envelope = make_envelope(_OrderPlaced(order_id='o-7'))

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id == 'o-7'
        assert outbox.saved[0].sequence_number == 1
        assert allocator.calls == ['o-7']

    @staticmethod
    async def test_keyless_message_skips_sequence_allocation() -> None:
        outbox = FakeOutboxStore()
        codec = _make_codec()
        allocator = RecordingAllocator()
        async with make_async_container(_TestDepsProvider(outbox, codec, allocator)) as container:
            endpoint = ExternalEndpoint(uri='test://out', partition_by=None)
            envelope = make_envelope(_OrderPlaced(order_id='o-11'))

            await endpoint.dispatch(envelope, container)

        assert outbox.saved[0].group_id is None
        assert outbox.saved[0].sequence_number is None
        assert allocator.calls == []
