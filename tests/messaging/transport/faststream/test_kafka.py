# ruff: noqa: SLF001
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from typing_extensions import override

pytest.importorskip('faststream.kafka')

from aiokafka.structs import TopicPartition
from faststream.kafka import KafkaBroker, TestKafkaBroker

from waku.messaging.transport.faststream.kafka import (
    DefaultKafkaEnvelopeMapper,
    FastStreamKafkaTransport,
    IKafkaEnvelopeMapper,
    KafkaOutgoing,
    KafkaSubscription,
    kafka_transport,
)
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import EnvelopeMetadata, Subscription
from waku.messaging.transport.mapping import WIRE_CONTENT_TYPE

if TYPE_CHECKING:
    from faststream.kafka.annotations import KafkaMessage
    from pytest_mock import MockerFixture

_METADATA = EnvelopeMetadata(
    message_id='mid-1',
    correlation_id='corr-1',
    causation_id='cause-1',
    message_type='evt',
    group_id='order-1',
)


def _make_transport() -> tuple[KafkaBroker, FastStreamKafkaTransport]:
    broker = KafkaBroker('localhost:9092')
    return broker, FastStreamKafkaTransport(broker=broker, consumer_group='svc')


class _FakeConsumer:
    def __init__(self) -> None:
        self.assigned = (TopicPartition('orders', 0), TopicPartition('orders', 1))
        self.paused: list[tuple[TopicPartition, ...]] = []
        self.resumed: list[tuple[TopicPartition, ...]] = []

    def assignment(self) -> tuple[TopicPartition, ...]:
        return self.assigned

    def pause(self, *partitions: TopicPartition) -> None:
        self.paused.append(partitions)

    def resume(self, *partitions: TopicPartition) -> None:
        self.resumed.append(partitions)


class TestFastStreamKafkaTransportSend:
    @staticmethod
    async def test_send_maps_group_id_to_the_kafka_message_key(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({'hello': 'world'}, destination='orders', metadata=_METADATA)

        publish.assert_awaited_once_with(
            {'hello': 'world'},
            'orders',
            key=b'order-1',
            headers=DefaultKafkaEnvelopeMapper().map_outgoing({'hello': 'world'}, _METADATA).headers,
        )

    @staticmethod
    async def test_send_without_group_id_uses_no_partition_key(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        metadata = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='evt')
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({'hello': 'world'}, destination='orders', metadata=metadata)

        publish.assert_awaited_once_with(
            {'hello': 'world'},
            'orders',
            key=None,
            headers=DefaultKafkaEnvelopeMapper().map_outgoing({'hello': 'world'}, metadata).headers,
        )

    @staticmethod
    async def test_custom_mapper_partition_is_threaded_to_publish(mocker: MockerFixture) -> None:
        class _PartitionMapper(IKafkaEnvelopeMapper):
            @override
            def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> KafkaOutgoing:
                return KafkaOutgoing(
                    body=payload,
                    key=None,
                    headers={},
                    partition=7,
                )

            @override
            async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
                raise NotImplementedError

        broker = KafkaBroker('localhost:9092')
        transport = FastStreamKafkaTransport(broker=broker, consumer_group='svc', mapper=_PartitionMapper())
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({}, destination='t', metadata=_METADATA)

        assert publish.await_args is not None
        assert publish.await_args.kwargs.get('partition') == 7

    @staticmethod
    async def test_default_mapper_omits_partition_from_publish(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({}, destination='t', metadata=_METADATA)

        assert publish.await_args is not None
        assert 'partition' not in publish.await_args.kwargs

    @staticmethod
    async def test_per_call_mapper_overrides_transport_default(mocker: MockerFixture) -> None:
        class _OverrideMapper(IKafkaEnvelopeMapper):
            @override
            def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> KafkaOutgoing:
                return KafkaOutgoing(body=payload, key=None, headers={}, partition=42)

            @override
            async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
                raise NotImplementedError  # pragma: no cover

        broker = KafkaBroker('localhost:9092')
        transport = FastStreamKafkaTransport(broker=broker, consumer_group='svc')
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({}, destination='orders', metadata=_METADATA, mapper=_OverrideMapper())

        assert publish.await_args is not None
        assert publish.await_args.kwargs.get('partition') == 42

    @staticmethod
    async def test_no_per_call_mapper_uses_transport_default(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({}, destination='orders', metadata=_METADATA)

        assert publish.await_args is not None
        # Default mapper sets no partition — partition kwarg must be absent.
        assert 'partition' not in publish.await_args.kwargs


_WIRE_HEADERS: dict[str, str] = {
    'message_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    'correlation_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    'causation_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
    'message_type': 'orders.OrderPlaced',
    'content-type': WIRE_CONTENT_TYPE,
}


class _FakeRawMessage:
    key: bytes | None = None


class _FakeInboundMessage:
    def __init__(
        self,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        decode_error: Exception | None = None,
    ) -> None:
        self._body: dict[str, Any] = body if body is not None else {'k': 'v'}
        self.headers: dict[str, str] = headers if headers is not None else dict(_WIRE_HEADERS)
        self._decode_error = decode_error
        self.raw_message = _FakeRawMessage()
        self.acked = 0
        self.nacked = 0
        self.rejected = 0

    async def decode(self) -> dict[str, Any]:
        if self._decode_error is not None:
            raise self._decode_error
        return self._body

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self) -> None:
        self.nacked += 1

    async def reject(self) -> None:
        self.rejected += 1


class TestDispatchInbound:
    @staticmethod
    async def test_ack_disposition_commits_via_ack() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, Any], EnvelopeMetadata]] = []
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert len(seen) == 1
        payload, metadata = seen[0]
        assert payload == {'k': 'v'}
        assert metadata.message_type == 'orders.OrderPlaced'
        assert (msg.acked, msg.nacked, msg.rejected) == (1, 0, 0)

    @staticmethod
    async def test_nack_requeue_disposition_seeks_back_via_nack() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(_payload: dict[str, Any], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 1, 0)

    @staticmethod
    async def test_reject_disposition_commits_via_reject() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(_payload: dict[str, Any], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 0, 1)

    @staticmethod
    async def test_raised_handler_seeks_back_via_nack() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(_payload: dict[str, Any], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            error = 'handler boom'
            raise RuntimeError(error)

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 1, 0)

    @staticmethod
    async def test_undecodable_payload_is_rejected_not_seek_backed() -> None:
        # Poison (foreign/corrupt wire format): commit/skip via reject(), never nack (seek-back = poison loop)
        # and never leave unhandled (no commit = redelivery).
        _, transport = _make_transport()
        msg = _FakeInboundMessage(decode_error=ValueError('bad payload'))
        seen: list[object] = []
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(payload: dict[str, Any], metadata: object) -> ConsumeDisposition:  # noqa: ARG001, RUF029
            seen.append(payload)
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 0, 1)
        assert seen == []

    @staticmethod
    async def test_foreign_content_type_is_rejected_via_mapper() -> None:
        # A foreign content-type causes map_incoming → UnsupportedContentTypeError → poison reject (commit/skip).
        _, transport = _make_transport()
        foreign_headers = {**_WIRE_HEADERS, 'content-type': 'application/octet-stream'}
        msg = _FakeInboundMessage(headers=foreign_headers)
        seen: list[object] = []
        mapper = DefaultKafkaEnvelopeMapper()

        async def on_message(payload: dict[str, Any], metadata: object) -> ConsumeDisposition:  # noqa: ARG001, RUF029
            seen.append(payload)
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 0, 1)
        assert seen == []


class TestKafkaSubscriptionPause:
    @staticmethod
    async def test_pause_then_resume_drive_the_live_consumer_and_are_idempotent() -> None:
        fake = _FakeConsumer()
        subscription = KafkaSubscription(lambda: fake)

        await subscription.pause()
        await subscription.pause()  # idempotent: already paused
        await subscription.resume()
        await subscription.resume()  # idempotent: already running

        assert fake.paused == [fake.assigned]
        assert fake.resumed == [fake.assigned]

    @staticmethod
    async def test_pause_is_a_safe_noop_before_the_consumer_starts() -> None:
        # consumer is None until broker.start(); pause must not raise.
        subscription = KafkaSubscription(lambda: None)

        await subscription.pause()

    @staticmethod
    async def test_resume_is_a_safe_noop_if_the_consumer_is_lost_while_paused() -> None:
        fake = _FakeConsumer()
        consumer: _FakeConsumer | None = fake
        subscription = KafkaSubscription(lambda: consumer)
        await subscription.pause()

        consumer = None  # broker tore the consumer down while paused (shutdown race)
        await subscription.resume()

        assert fake.resumed == []


class TestFastStreamKafkaTransportStartStop:
    @staticmethod
    async def test_start_is_idempotent(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        mock_start = mocker.patch.object(broker, 'start', new_callable=mocker.AsyncMock)

        await transport.start()
        await transport.start()

        mock_start.assert_awaited_once()

    @staticmethod
    async def test_stop_stops_the_broker_only_after_start(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        mocker.patch.object(broker, 'start', new_callable=mocker.AsyncMock)
        mock_stop = mocker.patch.object(broker, 'stop', new_callable=mocker.AsyncMock)

        await transport.stop()  # never started: no-op
        mock_stop.assert_not_awaited()

        await transport.start()
        await transport.stop()
        mock_stop.assert_awaited_once()


class TestKafkaTransportFactory:
    @staticmethod
    def test_kafka_transport_returns_a_factory_building_a_kafka_transport() -> None:
        factory = kafka_transport('localhost:9092', consumer_group='svc')
        transport = factory()
        assert isinstance(transport, FastStreamKafkaTransport)


class TestDefaultKafkaEnvelopeMapperOutgoing:
    @staticmethod
    def test_map_outgoing_sets_key_from_group_id() -> None:
        mapper = DefaultKafkaEnvelopeMapper()
        out = mapper.map_outgoing({'k': 'v'}, _METADATA)

        assert isinstance(out, KafkaOutgoing)
        assert out.key == b'order-1'
        assert out.body == {'k': 'v'}

    @staticmethod
    def test_map_outgoing_key_is_none_when_group_id_is_none() -> None:
        mapper = DefaultKafkaEnvelopeMapper()
        meta = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='t')
        out = mapper.map_outgoing({}, meta)

        assert out.key is None

    @staticmethod
    def test_map_outgoing_headers_include_group_id_when_set() -> None:
        mapper = DefaultKafkaEnvelopeMapper()
        out = mapper.map_outgoing({}, _METADATA)

        assert out.headers['group_id'] == 'order-1'

    @staticmethod
    def test_map_outgoing_headers_include_content_type() -> None:
        mapper = DefaultKafkaEnvelopeMapper()
        out = mapper.map_outgoing({}, _METADATA)

        assert out.headers['content-type'] == WIRE_CONTENT_TYPE

    @staticmethod
    def test_map_outgoing_headers_have_no_h_prefix() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            headers={'custom': 'val'},
        )
        mapper = DefaultKafkaEnvelopeMapper()
        out = mapper.map_outgoing({}, meta)

        assert 'custom' in out.headers
        assert not any(k.startswith('h.') for k in out.headers)

    @staticmethod
    def test_map_outgoing_user_header_colliding_with_reserved_is_skipped() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='real',
            headers={'message_type': 'user-attempt'},
        )
        mapper = DefaultKafkaEnvelopeMapper()
        out = mapper.map_outgoing({}, meta)

        assert out.headers['message_type'] == 'real'


class TestDefaultKafkaEnvelopeMapperIncoming:
    @staticmethod
    async def test_map_incoming_reads_payload_and_metadata(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={'order_id': 42})
        msg.headers = {
            'message_id': 'mid',
            'correlation_id': 'corr',
            'causation_id': 'cause',
            'message_type': 'orders.OrderPlaced',
            'message_version': '2',
            'content-type': WIRE_CONTENT_TYPE,
        }
        raw_msg = mocker.MagicMock()
        raw_msg.key = None
        msg.raw_message = raw_msg

        mapper = DefaultKafkaEnvelopeMapper()
        payload, meta = await mapper.map_incoming(msg)

        assert payload == {'order_id': 42}
        assert meta.message_id == 'mid'
        assert meta.message_type == 'orders.OrderPlaced'
        assert meta.message_version == 2

    @staticmethod
    async def test_map_incoming_kafka_key_takes_precedence_over_group_id_header(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
            'group_id': 'header-group',
        }
        raw_msg = mocker.MagicMock()
        raw_msg.key = b'key-group'
        msg.raw_message = raw_msg

        mapper = DefaultKafkaEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        # Key on the raw message takes precedence — overrides the header value
        assert meta.group_id == 'key-group'

    @staticmethod
    async def test_map_incoming_group_id_from_header_when_key_is_none(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
            'group_id': 'header-group',
        }
        raw_msg = mocker.MagicMock()
        raw_msg.key = None
        msg.raw_message = raw_msg

        mapper = DefaultKafkaEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        assert meta.group_id == 'header-group'

    @staticmethod
    async def test_map_incoming_group_id_is_none_when_both_key_and_header_absent(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
        }
        raw_msg = mocker.MagicMock()
        raw_msg.key = None
        msg.raw_message = raw_msg

        mapper = DefaultKafkaEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        assert meta.group_id is None

    @staticmethod
    async def test_map_incoming_empty_bytes_key_falls_back_to_header_group_id(mocker: MockerFixture) -> None:
        # W1 guard: an empty-bytes key b'' must NOT override the header group_id with ''.
        # Real aiokafka yields None for keyless messages; b'' is an edge case that should be ignored.
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
            'group_id': 'header-group',
        }
        raw_msg = mocker.MagicMock()
        raw_msg.key = b''
        msg.raw_message = raw_msg

        mapper = DefaultKafkaEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        # Empty-bytes key is falsy — header group_id is preserved, not replaced with ''.
        assert meta.group_id == 'header-group'


_CUSTOM_PAYLOAD: dict[str, Any] = {'custom': True}
_CUSTOM_METADATA = EnvelopeMetadata(
    message_id='custom-id',
    correlation_id='custom-corr',
    causation_id='custom-cause',
    message_type='custom.Type',
)


class _CustomKafkaMapper(IKafkaEnvelopeMapper):
    # Stub mapper returning a fixed distinctive payload+metadata so tests can assert on the output.

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> KafkaOutgoing:
        return KafkaOutgoing(body=payload, key=None, headers={})

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        return _CUSTOM_PAYLOAD, _CUSTOM_METADATA


class TestDispatchInboundWithCustomMapper:
    @staticmethod
    async def test_custom_mapper_output_reaches_on_message() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, Any], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        custom = _CustomKafkaMapper()
        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, custom)

        assert len(seen) == 1
        payload, metadata = seen[0]
        # Observable: the custom mapper's distinctive output was what on_message received.
        assert payload is _CUSTOM_PAYLOAD
        assert metadata is _CUSTOM_METADATA

    @staticmethod
    async def test_default_mapper_used_when_no_override() -> None:
        _, transport = _make_transport()
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, Any], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        default_mapper = DefaultKafkaEnvelopeMapper()
        await transport._dispatch_inbound(cast('KafkaMessage', msg), on_message, default_mapper)

        payload, metadata = seen[0]
        # Default Wolverine mapper decodes from the fake message body, not the custom stub output.
        assert payload == {'k': 'v'}
        assert metadata.message_type == 'orders.OrderPlaced'


class TestFastStreamKafkaTransportSubscribeMapper:
    @staticmethod
    async def test_custom_mapper_output_reaches_on_message_via_subscribe() -> None:
        # Observable end-to-end proof: custom mapper passed to subscribe() drives the inbound handler,
        # not the transport-level default. Verified by publishing through TestKafkaBroker and asserting
        # that on_message receives exactly _CUSTOM_PAYLOAD / _CUSTOM_METADATA (not Wolverine-decoded output).
        broker, transport = _make_transport()
        seen: list[tuple[dict[str, Any], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        custom = _CustomKafkaMapper()
        transport.subscribe('orders', on_message, mapper=custom)

        async with TestKafkaBroker(broker):
            await broker.publish({'any': 'body'}, 'orders')

        assert len(seen) == 1
        payload, metadata = seen[0]
        assert payload is _CUSTOM_PAYLOAD
        assert metadata is _CUSTOM_METADATA

    @staticmethod
    def test_subscribe_without_mapper_uses_transport_default() -> None:
        # Structural: subscribe without mapper kwarg must not raise and returns a Subscription.
        _, transport = _make_transport()

        async def on_message(_payload: dict[str, Any], _metadata: Any) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        sub = transport.subscribe('orders', on_message)
        assert isinstance(sub, Subscription)
