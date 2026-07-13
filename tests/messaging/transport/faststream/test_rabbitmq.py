# ruff: noqa: SLF001
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
from typing_extensions import override

pytest.importorskip('faststream.rabbit')

from faststream.rabbit import RabbitBroker, TestRabbitBroker

from waku.messaging.transport.faststream.rabbitmq import (
    DefaultRabbitEnvelopeMapper,
    FastStreamRabbitTransport,
    IRabbitEnvelopeMapper,
    RabbitOutgoing,
    rabbit_transport,
)
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import EnvelopeMetadata, Subscription
from waku.messaging.transport.mapping import WIRE_CONTENT_TYPE

if TYPE_CHECKING:
    from faststream.rabbit.annotations import RabbitMessage
    from pytest_mock import MockerFixture

_METADATA = EnvelopeMetadata(
    message_id='mid-1',
    correlation_id='corr-1',
    causation_id='cause-1',
    message_type='evt',
)


class TestFastStreamRabbitTransportSend:
    @staticmethod
    async def test_send_routes_through_mapper_and_calls_publish(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({'payload': {'value': 'hello'}}, destination='q', metadata=_METADATA)

        out = DefaultRabbitEnvelopeMapper().map_outgoing({'payload': {'value': 'hello'}}, _METADATA)
        publish.assert_awaited_once_with(
            out.body,
            'q',
            headers=out.headers,
            persist=True,
        )

    @staticmethod
    async def test_send_publishes_persistent_by_default(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA)

        assert publish.await_args is not None
        assert publish.await_args.kwargs['persist'] is True

    @staticmethod
    async def test_send_threads_expiration_to_publish(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='ca',
            message_type='evt',
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

        await t.send({}, destination='q', metadata=meta)

        assert publish.await_args is not None
        assert publish.await_args.kwargs['expiration'] == meta.expires_at

    @staticmethod
    async def test_custom_mapper_can_opt_out_of_persistence(mocker: MockerFixture) -> None:
        class _NonPersistentMapper(IRabbitEnvelopeMapper):
            @override
            def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
                return RabbitOutgoing(body=payload, headers={}, persist=False)

            @override
            async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
                raise NotImplementedError  # pragma: no cover

        t = FastStreamRabbitTransport(url='amqp://x', mapper=_NonPersistentMapper())
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA)

        assert publish.await_args is not None
        assert publish.await_args.kwargs['persist'] is False

    @staticmethod
    async def test_send_group_id_as_header_when_set(mocker: MockerFixture) -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            group_id='order-99',
        )
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=meta)

        assert publish.await_args is not None
        assert publish.await_args.kwargs['headers']['group_id'] == 'order-99'

    @staticmethod
    async def test_custom_mapper_priority_is_threaded_to_publish(mocker: MockerFixture) -> None:
        class _PriorityMapper(IRabbitEnvelopeMapper):
            @override
            def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
                return RabbitOutgoing(
                    body=payload,
                    headers={},
                    priority=5,
                )

            @override
            async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
                raise NotImplementedError

        t = FastStreamRabbitTransport(url='amqp://x', mapper=_PriorityMapper())
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA)

        assert publish.await_args is not None
        assert publish.await_args.kwargs.get('priority') == 5

    @staticmethod
    async def test_default_mapper_omits_priority_from_publish(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA)

        assert publish.await_args is not None
        assert 'priority' not in publish.await_args.kwargs

    @staticmethod
    async def test_per_call_mapper_overrides_transport_default(mocker: MockerFixture) -> None:
        class _OverrideMapper(IRabbitEnvelopeMapper):
            @override
            def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
                return RabbitOutgoing(body=payload, headers={}, priority=9)

            @override
            async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
                raise NotImplementedError  # pragma: no cover

        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA, mapper=_OverrideMapper())

        assert publish.await_args is not None
        assert publish.await_args.kwargs.get('priority') == 9

    @staticmethod
    async def test_no_per_call_mapper_uses_transport_default(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        publish = mocker.patch.object(t._send_broker, 'publish', new_callable=mocker.AsyncMock)

        await t.send({}, destination='q', metadata=_METADATA)

        assert publish.await_args is not None
        # Default mapper sets no priority — priority kwarg must be absent.
        assert 'priority' not in publish.await_args.kwargs


_WIRE_HEADERS: dict[str, str] = {
    'message_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    'correlation_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    'causation_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
    'message_type': 'orders.OrderPlaced',
    'content-type': WIRE_CONTENT_TYPE,
}


class _FakeInboundMessage:
    def __init__(
        self,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        decode_error: Exception | None = None,
    ) -> None:
        self._body: dict[str, object] = body if body is not None else {'k': 'v'}
        self.headers: dict[str, str] = headers if headers is not None else dict(_WIRE_HEADERS)
        self._decode_error = decode_error
        self.acked = 0
        self.rejected = 0
        self.nack_requeues: list[bool] = []

    async def decode(self) -> dict[str, object]:
        if self._decode_error is not None:
            raise self._decode_error
        return self._body

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, *, requeue: bool) -> None:
        self.nack_requeues.append(requeue)

    async def reject(self) -> None:
        self.rejected += 1


class TestDispatchInbound:
    @staticmethod
    async def test_ack_disposition_acks() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, object], EnvelopeMetadata]] = []
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(payload: dict[str, object], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert len(seen) == 1
        payload, metadata = seen[0]
        assert payload == {'k': 'v'}
        assert metadata.message_type == 'orders.OrderPlaced'
        assert (msg.acked, msg.nack_requeues, msg.rejected) == (1, [], 0)

    @staticmethod
    async def test_nack_requeue_disposition_requeues() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(_payload: dict[str, object], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [True], 0)

    @staticmethod
    async def test_reject_disposition_rejects() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(_payload: dict[str, object], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [], 1)

    @staticmethod
    async def test_raised_handler_requeues() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(_payload: dict[str, object], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            error = 'handler boom'
            raise RuntimeError(error)

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [True], 0)

    @staticmethod
    async def test_undecodable_payload_is_rejected_without_requeue() -> None:
        # Poison (foreign/corrupt wire format): reject without requeue (-> DLX/drop), never requeue (poison loop)
        # and never leave unacked (redelivery on reconnect).
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage(decode_error=ValueError('bad payload'))
        seen: list[object] = []
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(payload: dict[str, object], metadata: object) -> ConsumeDisposition:  # noqa: ARG001, RUF029
            seen.append(payload)
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [], 1)
        assert seen == []

    @staticmethod
    async def test_foreign_content_type_is_rejected_via_mapper() -> None:
        # A foreign content-type causes map_incoming → UnsupportedContentTypeError → poison reject.
        transport = FastStreamRabbitTransport(url='amqp://x')
        foreign_headers = {**_WIRE_HEADERS, 'content-type': 'application/octet-stream'}
        msg = _FakeInboundMessage(headers=foreign_headers)
        seen: list[object] = []
        mapper = DefaultRabbitEnvelopeMapper()

        async def on_message(payload: dict[str, object], metadata: object) -> ConsumeDisposition:  # noqa: ARG001, RUF029
            seen.append(payload)
            return ConsumeDisposition.ACK

        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, mapper)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [], 1)
        assert seen == []


class TestFastStreamRabbitTransportSubscription:
    @staticmethod
    async def test_repeated_pause_resume_do_not_raise(mocker: MockerFixture) -> None:
        # Whether pause() actually stops broker delivery is NOT observable under TestRabbitBroker — its
        # FakeProducer routes by routing key and ignores a stopped subscriber — so that behaviour is covered
        # end-to-end in test_listener_backpressure_integration.py. This test only pins that repeated calls do
        # not raise.
        t = FastStreamRabbitTransport(url='amqp://x')

        async def on_message(_payload: dict[str, object], _metadata: object) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        # Obtain the Subscription handle; inject a mock subscriber so stop()/start() can be awaited without a
        # live broker. The returned object is a _FastStreamSubscription whose _subscriber is the FastStream
        # subscriber built by broker.subscriber(); replace it with a mock for unit isolation.
        sub = t.subscribe('orders', on_message)
        fake_subscriber = mocker.MagicMock()
        fake_subscriber.stop = mocker.AsyncMock()
        fake_subscriber.start = mocker.AsyncMock()
        cast('Any', sub)._subscriber = fake_subscriber

        await sub.pause()
        await sub.pause()  # idempotent: already paused
        await sub.resume()
        await sub.resume()  # idempotent: already running

        fake_subscriber.stop.assert_awaited_once()
        fake_subscriber.start.assert_awaited_once()


class TestFastStreamRabbitTransportStartStop:
    @staticmethod
    async def test_start_sends_before_listen_and_both_awaited_once(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        call_order: list[str] = []

        async def send_start() -> None:  # noqa: RUF029
            call_order.append('send')

        async def listen_start() -> None:  # noqa: RUF029
            call_order.append('listen')

        mock_send_start = mocker.patch.object(t._send_broker, 'start', side_effect=send_start)
        mock_listen_start = mocker.patch.object(t._listen_broker, 'start', side_effect=listen_start)

        await t.start()

        assert call_order == ['send', 'listen']
        mock_send_start.assert_awaited_once()
        mock_listen_start.assert_awaited_once()

    @staticmethod
    async def test_start_is_idempotent(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        mock_send_start = mocker.patch.object(t._send_broker, 'start', new_callable=mocker.AsyncMock)
        mock_listen_start = mocker.patch.object(t._listen_broker, 'start', new_callable=mocker.AsyncMock)

        await t.start()
        await t.start()

        mock_send_start.assert_awaited_once()
        mock_listen_start.assert_awaited_once()

    @staticmethod
    async def test_stop_stops_listen_then_send(mocker: MockerFixture) -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        t._started = True

        call_order: list[str] = []

        async def listen_stop() -> None:  # noqa: RUF029
            call_order.append('listen')

        async def send_stop() -> None:  # noqa: RUF029
            call_order.append('send')

        mocker.patch.object(t._listen_broker, 'stop', side_effect=listen_stop)
        mocker.patch.object(t._send_broker, 'stop', side_effect=send_stop)

        await t.stop()

        assert call_order == ['listen', 'send']


class TestRabbitTransportFactory:
    @staticmethod
    def test_rabbit_transport_returns_factory_building_a_transport() -> None:
        factory = rabbit_transport('amqp://x', prefetch_count=100)
        t = factory()
        assert isinstance(t, FastStreamRabbitTransport)
        assert t._prefetch_count == 100
        assert isinstance(t._send_broker, RabbitBroker)
        assert isinstance(t._listen_broker, RabbitBroker)
        assert t._send_broker is not t._listen_broker


class TestDefaultRabbitEnvelopeMapperOutgoing:
    @staticmethod
    def test_map_outgoing_returns_rabbit_outgoing_with_headers() -> None:
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({'order_id': 1}, _METADATA)

        assert isinstance(out, RabbitOutgoing)
        assert out.body == {'order_id': 1}
        assert out.headers['message_id'] == 'mid-1'
        assert out.headers['content-type'] == WIRE_CONTENT_TYPE

    @staticmethod
    def test_default_rabbit_outgoing_persists() -> None:
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({}, _METADATA)

        assert out.persist is True

    @staticmethod
    def test_map_outgoing_no_key_field_on_rabbit_outgoing() -> None:
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({}, _METADATA)

        assert not hasattr(out, 'key')

    @staticmethod
    def test_map_outgoing_group_id_emitted_as_header() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            group_id='order-99',
        )
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({}, meta)

        assert out.headers['group_id'] == 'order-99'

    @staticmethod
    def test_map_outgoing_headers_bare_no_h_prefix() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            headers={'custom': 'val'},
        )
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({}, meta)

        assert 'custom' in out.headers
        assert not any(k.startswith('h.') for k in out.headers)

    @staticmethod
    def test_map_outgoing_reserved_user_header_skipped() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='real',
            headers={'message_type': 'user-attempt'},
        )
        mapper = DefaultRabbitEnvelopeMapper()
        out = mapper.map_outgoing({}, meta)

        assert out.headers['message_type'] == 'real'

    @staticmethod
    def test_default_mapper_maps_expires_at_to_expiration() -> None:
        # A delivery deadline reaches the AMQP per-message TTL (Wolverine RabbitMqEnvelopeMapper parity).
        # The absolute datetime is forwarded; aio_pika encodes it to a relative-ms TTL at publish.
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='ca',
            message_type='evt',
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
        out = DefaultRabbitEnvelopeMapper().map_outgoing({'v': 1}, meta)

        assert out.expiration == meta.expires_at

    @staticmethod
    def test_default_mapper_leaves_expiration_none_without_deadline() -> None:
        out = DefaultRabbitEnvelopeMapper().map_outgoing({}, _METADATA)

        assert out.expiration is None


class TestDefaultRabbitEnvelopeMapperIncoming:
    @staticmethod
    async def test_map_incoming_reads_payload_and_metadata(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={'value': 'x'})
        msg.headers = {
            'message_id': 'mid',
            'correlation_id': 'corr',
            'causation_id': 'cause',
            'message_type': 'orders.OrderPlaced',
            'message_version': '3',
            'content-type': WIRE_CONTENT_TYPE,
        }

        mapper = DefaultRabbitEnvelopeMapper()
        payload, meta = await mapper.map_incoming(msg)

        assert payload == {'value': 'x'}
        assert meta.message_id == 'mid'
        assert meta.message_version == 3

    @staticmethod
    async def test_map_incoming_group_id_from_header(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
            'group_id': 'rabbit-group',
        }

        mapper = DefaultRabbitEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        assert meta.group_id == 'rabbit-group'

    @staticmethod
    async def test_map_incoming_group_id_is_none_when_absent(mocker: MockerFixture) -> None:
        msg = mocker.MagicMock()
        msg.decode = mocker.AsyncMock(return_value={})
        msg.headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': WIRE_CONTENT_TYPE,
        }

        mapper = DefaultRabbitEnvelopeMapper()
        _, meta = await mapper.map_incoming(msg)

        assert meta.group_id is None


_CUSTOM_PAYLOAD: dict[str, object] = {'custom': True}
_CUSTOM_METADATA = EnvelopeMetadata(
    message_id='custom-id',
    correlation_id='custom-corr',
    causation_id='custom-cause',
    message_type='custom.Type',
)


class _CustomRabbitMapper(IRabbitEnvelopeMapper):
    # Stub mapper returning a fixed distinctive payload+metadata so tests can assert on the output.

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
        return RabbitOutgoing(body=payload, headers={})

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, object], EnvelopeMetadata]:
        return _CUSTOM_PAYLOAD, _CUSTOM_METADATA


class TestDispatchInboundWithCustomMapper:
    @staticmethod
    async def test_custom_mapper_output_reaches_on_message() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, object], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, object], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        custom = _CustomRabbitMapper()
        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, custom)

        assert len(seen) == 1
        payload, metadata = seen[0]
        # Observable: the custom mapper's distinctive output was what on_message received.
        assert payload is _CUSTOM_PAYLOAD
        assert metadata is _CUSTOM_METADATA

    @staticmethod
    async def test_default_mapper_used_when_no_override() -> None:
        transport = FastStreamRabbitTransport(url='amqp://x')
        msg = _FakeInboundMessage()
        seen: list[tuple[dict[str, object], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, object], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        default_mapper = DefaultRabbitEnvelopeMapper()
        await transport._dispatch_inbound(cast('RabbitMessage', msg), on_message, default_mapper)

        payload, metadata = seen[0]
        # Default Wolverine mapper decodes from the fake message body, not the custom stub output.
        assert payload == {'k': 'v'}
        assert metadata.message_type == 'orders.OrderPlaced'


class TestFastStreamRabbitTransportSubscribeMapper:
    @staticmethod
    async def test_custom_mapper_output_reaches_on_message_via_subscribe() -> None:
        # Observable end-to-end proof: custom mapper passed to subscribe() drives the inbound handler,
        # not the transport-level default. Verified by publishing through TestRabbitBroker and asserting
        # that on_message receives exactly _CUSTOM_PAYLOAD / _CUSTOM_METADATA (not Wolverine-decoded output).
        t = FastStreamRabbitTransport(url='amqp://x')
        seen: list[tuple[dict[str, object], EnvelopeMetadata]] = []

        async def on_message(payload: dict[str, object], metadata: EnvelopeMetadata) -> ConsumeDisposition:  # noqa: RUF029
            seen.append((payload, metadata))
            return ConsumeDisposition.ACK

        custom = _CustomRabbitMapper()
        t.subscribe('orders', on_message, mapper=custom)

        async with TestRabbitBroker(t._send_broker, t._listen_broker):
            await t._listen_broker.publish({'any': 'body'}, 'orders')

        assert len(seen) == 1
        payload, metadata = seen[0]
        assert payload is _CUSTOM_PAYLOAD
        assert metadata is _CUSTOM_METADATA

    @staticmethod
    def test_subscribe_without_mapper_uses_transport_default() -> None:
        # Structural: subscribe without mapper kwarg must not raise and returns a Subscription.
        t = FastStreamRabbitTransport(url='amqp://x')

        async def on_message(_payload: dict[str, object], _metadata: Any) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        sub = t.subscribe('orders', on_message)
        assert isinstance(sub, Subscription)
