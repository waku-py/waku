# ruff: noqa: SLF001
import pytest

pytest.importorskip('faststream.rabbit')

from unittest.mock import AsyncMock, patch

from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.message import RabbitMessage

from waku.messaging.transport.faststream.rabbitmq import FastStreamRabbitTransport, rabbit_transport
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


class TestFastStreamRabbitTransportDispositionMapping:
    @staticmethod
    async def test_nack_requeue_disposition_calls_nack_with_requeue() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        t.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'nack', new_callable=AsyncMock) as mock_nack:
            async with TestRabbitBroker(t._listen_broker):
                # The subscribe body lives on _listen_broker; publishing directly to it
                # makes a single-broker TestRabbitBroker context sufficient for disposition coverage.
                await t._listen_broker.publish({'k': 'v'}, 'q')

            mock_nack.assert_awaited_once_with(requeue=True)

    @staticmethod
    async def test_reject_disposition_calls_reject() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        t.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'reject', new_callable=AsyncMock) as mock_reject:
            async with TestRabbitBroker(t._listen_broker):
                await t._listen_broker.publish({'k': 'v'}, 'q')

            mock_reject.assert_awaited_once()

    @staticmethod
    async def test_raised_handler_nacks_requeue() -> None:
        t = FastStreamRabbitTransport(url='amqp://x')

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            msg = 'handler boom'
            raise RuntimeError(msg)

        t.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'nack', new_callable=AsyncMock) as mock_nack:
            async with TestRabbitBroker(t._listen_broker):
                await t._listen_broker.publish({'k': 'v'}, 'q')

            mock_nack.assert_awaited_once_with(requeue=True)


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
