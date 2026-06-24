# ruff: noqa: SLF001
import pytest

pytest.importorskip('faststream.rabbit')

from unittest.mock import AsyncMock, patch

from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.message import RabbitMessage

from waku.messaging.transport.faststream.rabbitmq import (
    FastStreamRabbitTransport,
    dispatch_inbound,
    rabbit_transport,
)
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import WireMetadata

_METADATA = WireMetadata(
    message_id='mid-1',
    correlation_id='corr-1',
    causation_id='cause-1',
    message_type='evt',
)


class TestFastStreamRabbitTransportRoundTrip:
    @staticmethod
    async def test_send_then_consume_roundtrip() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        seen: list[dict[str, object]] = []

        async def on_message(body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            seen.append(body)
            return ConsumeDisposition.ACK

        t.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'ack', new_callable=AsyncMock) as mock_ack:
            # TestRabbitBroker(*brokers) cross-routes across all supplied brokers: its
            # FakeProducer searches `self.brokers` for matching subscribers (verified
            # against faststream 0.7.1 source — create_publisher_fake_subscriber iterates
            # `for handler in (s for b in self.brokers for s in b.subscribers)`).
            async with TestRabbitBroker(t._send_broker, t._listen_broker):
                await t.start()
                await t.send({'payload': {'value': 'hello'}}, destination='q', metadata=_METADATA)

            assert seen == [{'payload': {'value': 'hello'}}]
            mock_ack.assert_awaited_once()


class _FakeInboundMessage:
    def __init__(self, *, decode_error: Exception | None = None) -> None:
        self._decode_error = decode_error
        self.acked = 0
        self.rejected = 0
        self.nack_requeues: list[bool] = []

    async def decode(self) -> dict[str, object]:
        if self._decode_error is not None:
            raise self._decode_error
        return {'k': 'v'}

    async def ack(self) -> None:
        self.acked += 1

    async def nack(self, *, requeue: bool) -> None:
        self.nack_requeues.append(requeue)

    async def reject(self) -> None:
        self.rejected += 1


class TestDispatchInbound:
    @staticmethod
    async def test_ack_disposition_acks() -> None:
        msg = _FakeInboundMessage()
        seen: list[dict[str, object]] = []

        async def on_message(body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            seen.append(body)
            return ConsumeDisposition.ACK

        await dispatch_inbound(msg, on_message)

        assert seen == [{'k': 'v'}]
        assert (msg.acked, msg.nack_requeues, msg.rejected) == (1, [], 0)

    @staticmethod
    async def test_nack_requeue_disposition_requeues() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [True], 0)

    @staticmethod
    async def test_reject_disposition_rejects() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [], 1)

    @staticmethod
    async def test_raised_handler_requeues() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            error = 'handler boom'
            raise RuntimeError(error)

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [True], 0)

    @staticmethod
    async def test_undecodable_payload_is_rejected_without_requeue() -> None:
        # Poison (foreign/corrupt wire format): reject without requeue (-> DLX/drop), never requeue (poison loop)
        # and never leave unacked (redelivery on reconnect).
        msg = _FakeInboundMessage(decode_error=ValueError('bad payload'))
        seen: list[dict[str, object]] = []

        async def on_message(body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            seen.append(body)
            return ConsumeDisposition.ACK

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nack_requeues, msg.rejected) == (0, [], 1)
        assert seen == []


class TestFastStreamRabbitTransportSubscription:
    @staticmethod
    async def test_subscribe_registers_handler_and_repeated_pause_resume_do_not_raise() -> None:
        # Observable here: capturing `broker.subscriber(...)` then `subscriber(_handler)` registers the consumer (the
        # published message reaches the handler). Whether pause() actually stops broker delivery is NOT observable
        # under TestRabbitBroker — its FakeProducer routes by routing key and ignores a stopped subscriber — so that
        # behaviour is covered end-to-end in test_listener_backpressure_integration.py. The double pause/resume here
        # only pins that repeated calls do not raise.
        t = FastStreamRabbitTransport(url='amqp://x')
        received: list[dict[str, object]] = []

        async def on_message(body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            received.append(body)
            return ConsumeDisposition.ACK

        subscription = t.subscribe('orders', on_message)

        with patch.object(RabbitMessage, 'ack', new_callable=AsyncMock):
            async with TestRabbitBroker(t._send_broker, t._listen_broker):
                await t.start()
                await t.send({'payload': {'value': 'x'}}, destination='orders', metadata=_METADATA)
                await subscription.pause()
                await subscription.pause()  # a second stop must not raise
                await subscription.resume()
                await subscription.resume()  # a second start must not raise

        assert received == [{'payload': {'value': 'x'}}]


class TestFastStreamRabbitTransportStartStop:
    @staticmethod
    async def test_start_sends_before_listen_and_both_awaited_once() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        call_order: list[str] = []

        async def send_start() -> None:  # noqa: RUF029
            call_order.append('send')

        async def listen_start() -> None:  # noqa: RUF029
            call_order.append('listen')

        with (
            patch.object(t._send_broker, 'start', side_effect=send_start) as mock_send_start,
            patch.object(t._listen_broker, 'start', side_effect=listen_start) as mock_listen_start,
        ):
            await t.start()

        assert call_order == ['send', 'listen']
        mock_send_start.assert_awaited_once()
        mock_listen_start.assert_awaited_once()

    @staticmethod
    async def test_start_is_idempotent() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        with (
            patch.object(t._send_broker, 'start', new_callable=AsyncMock) as mock_send_start,
            patch.object(t._listen_broker, 'start', new_callable=AsyncMock) as mock_listen_start,
        ):
            await t.start()
            await t.start()

        mock_send_start.assert_awaited_once()
        mock_listen_start.assert_awaited_once()

    @staticmethod
    async def test_stop_stops_listen_then_send() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')
        t._started = True

        call_order: list[str] = []

        async def listen_stop() -> None:  # noqa: RUF029
            call_order.append('listen')

        async def send_stop() -> None:  # noqa: RUF029
            call_order.append('send')

        with (
            patch.object(t._listen_broker, 'stop', side_effect=listen_stop),
            patch.object(t._send_broker, 'stop', side_effect=send_stop),
        ):
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
