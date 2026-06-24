from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip('faststream.kafka')

from aiokafka.structs import TopicPartition
from faststream.kafka import KafkaBroker

from waku.messaging.transport.faststream.kafka import (
    FastStreamKafkaTransport,
    KafkaSubscription,
    dispatch_inbound,
    kafka_transport,
)
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import WireMetadata

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_METADATA = WireMetadata(
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
            headers=_METADATA.as_headers(),
        )

    @staticmethod
    async def test_send_without_group_id_uses_no_partition_key(mocker: MockerFixture) -> None:
        broker, transport = _make_transport()
        metadata = WireMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='evt')
        publish = mocker.patch.object(broker, 'publish', new_callable=mocker.AsyncMock)

        await transport.send({'hello': 'world'}, destination='orders', metadata=metadata)

        publish.assert_awaited_once_with(
            {'hello': 'world'},
            'orders',
            key=None,
            headers=metadata.as_headers(),
        )


class _FakeInboundMessage:
    def __init__(self, *, body: dict[str, Any] | None = None, decode_error: Exception | None = None) -> None:
        self._body: dict[str, Any] = body if body is not None else {'k': 'v'}
        self._decode_error = decode_error
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
        msg = _FakeInboundMessage()
        seen: list[dict[str, Any]] = []

        async def on_message(body: dict[str, Any]) -> ConsumeDisposition:  # noqa: RUF029
            seen.append(body)
            return ConsumeDisposition.ACK

        await dispatch_inbound(msg, on_message)

        assert seen == [{'k': 'v'}]
        assert (msg.acked, msg.nacked, msg.rejected) == (1, 0, 0)

    @staticmethod
    async def test_nack_requeue_disposition_seeks_back_via_nack() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, Any]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.NACK_REQUEUE

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 1, 0)

    @staticmethod
    async def test_reject_disposition_commits_via_reject() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, Any]) -> ConsumeDisposition:  # noqa: RUF029
            return ConsumeDisposition.REJECT

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 0, 1)

    @staticmethod
    async def test_raised_handler_seeks_back_via_nack() -> None:
        msg = _FakeInboundMessage()

        async def on_message(_body: dict[str, Any]) -> ConsumeDisposition:  # noqa: RUF029
            error = 'handler boom'
            raise RuntimeError(error)

        await dispatch_inbound(msg, on_message)

        assert (msg.acked, msg.nacked, msg.rejected) == (0, 1, 0)

    @staticmethod
    async def test_undecodable_payload_is_rejected_not_seek_backed() -> None:
        # Poison (foreign/corrupt wire format): commit/skip via reject(), never nack (seek-back = poison loop)
        # and never leave unhandled (no commit = redelivery).
        msg = _FakeInboundMessage(decode_error=ValueError('bad payload'))
        seen: list[dict[str, Any]] = []

        async def on_message(body: dict[str, Any]) -> ConsumeDisposition:  # noqa: RUF029
            seen.append(body)
            return ConsumeDisposition.ACK

        await dispatch_inbound(msg, on_message)

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
