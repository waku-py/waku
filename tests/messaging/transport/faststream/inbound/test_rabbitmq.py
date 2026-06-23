# ruff: noqa: E402
import pytest

faststream_rabbit = pytest.importorskip('faststream.rabbit')

from unittest.mock import AsyncMock, patch

from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.message import RabbitMessage

from waku.messaging.transport.faststream.inbound.rabbitmq import FastStreamRabbitInboundTransport
from waku.messaging.transport.inbound import ConsumeDisposition


class TestFastStreamRabbitInboundTransportAck:
    @staticmethod
    async def test_ack_disposition_calls_ack_on_rabbit_message() -> None:
        broker = RabbitBroker()
        transport = FastStreamRabbitInboundTransport(broker)

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        transport.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'ack', new_callable=AsyncMock) as mock_ack:
            async with TestRabbitBroker(broker):
                await broker.publish({'k': 'v'}, 'q')

            mock_ack.assert_awaited_once()

    @staticmethod
    async def test_nack_requeue_disposition_calls_nack_with_requeue_on_rabbit_message() -> None:
        broker = RabbitBroker()
        transport = FastStreamRabbitInboundTransport(broker)

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        transport.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'nack', new_callable=AsyncMock) as mock_nack:
            async with TestRabbitBroker(broker):
                await broker.publish({'k': 'v'}, 'q')

            mock_nack.assert_awaited_once_with(requeue=True)

    @staticmethod
    async def test_reject_disposition_calls_reject_on_rabbit_message() -> None:
        broker = RabbitBroker()
        transport = FastStreamRabbitInboundTransport(broker)

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        transport.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'reject', new_callable=AsyncMock) as mock_reject:
            async with TestRabbitBroker(broker):
                await broker.publish({'k': 'v'}, 'q')

            mock_reject.assert_awaited_once()

    @staticmethod
    async def test_handler_exception_nacks_requeue_on_rabbit_message() -> None:
        broker = RabbitBroker()
        transport = FastStreamRabbitInboundTransport(broker)

        async def on_message(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            msg = 'handler boom'
            raise RuntimeError(msg)

        transport.subscribe('q', on_message)

        with patch.object(RabbitMessage, 'nack', new_callable=AsyncMock) as mock_nack:
            async with TestRabbitBroker(broker):
                await broker.publish({'k': 'v'}, 'q')

            mock_nack.assert_awaited_once_with(requeue=True)


class TestFastStreamRabbitInboundTransportIdempotentStart:
    @staticmethod
    async def test_start_is_idempotent_broker_start_called_once() -> None:
        broker = RabbitBroker()
        transport = FastStreamRabbitInboundTransport(broker)

        async def on_message_a(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        async def on_message_b(_body: dict[str, object]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.ACK

        transport.subscribe('q1', on_message_a)
        transport.subscribe('q2', on_message_b)

        with patch.object(broker, 'start', new_callable=AsyncMock) as mock_start:
            await transport.start()
            await transport.start()

        mock_start.assert_awaited_once()
