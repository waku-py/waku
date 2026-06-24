"""Kafka-specific FastStream transport (aiokafka backend).

Broker-specific by design: the ``ConsumeDisposition`` -> ack/nack/reject mapping and the partition-key/commit
model do not generalise across brokers (e.g. Kafka ``reject()`` commits the offset, Kafka has no broker requeue).
Deliberately NOT re-exported from a generic package — a consumer opts in by importing it from this module.

One ``KafkaBroker`` (injected): Kafka's producer and consumer are already separate clients with separate
connections and no shared connection-level flow control, so the Rabbit two-connection isolation is unnecessary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, Protocol

from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.kafka.annotations import KafkaMessage  # noqa: TC002 -- runtime: fast_depends resolves the annotation
from typing_extensions import override

from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import ITransport, Subscription

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from aiokafka.structs import TopicPartition

    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import TransportFactory, WireMetadata

__all__ = ['FastStreamKafkaTransport', 'kafka_transport']

logger = logging.getLogger(__name__)


def _key(group_id: str | None) -> bytes | None:
    return group_id.encode('utf-8') if group_id is not None else None


class _InboundMessage(Protocol):
    """The broker-message surface the inbound dispatch needs: decode + the disposition acks."""

    async def decode(self) -> Any: ...
    async def ack(self) -> None: ...
    async def nack(self) -> None: ...
    async def reject(self) -> None: ...


async def dispatch_inbound(msg: _InboundMessage, on_message: ConsumeCallback) -> None:
    """Decode an inbound Kafka message and apply the handler's disposition, broker-honestly.

    Importable (not exported) so the decode/disposition logic is unit-testable without a live broker.
    """
    try:
        body: dict[str, Any] = await msg.decode()
    except Exception:
        # An undecodable payload is poison (foreign/corrupt wire format): commit/skip it via reject(). nacking
        # would seek-back into an infinite poison loop; leaving it unhandled never commits the offset (redelivery).
        logger.exception('Undecodable Kafka payload rejected (commit/skip) — not seek-backed')
        await msg.reject()
        return
    try:
        disposition = await on_message(body)
    except Exception:
        logger.exception('Inbound Kafka message handling failed; seeking back for re-read')
        await msg.nack()  # seek-back -> re-read on next poll (Kafka has no broker requeue)
        return
    if disposition is ConsumeDisposition.ACK:
        await msg.ack()  # commit offset
    elif disposition is ConsumeDisposition.NACK_REQUEUE:
        await msg.nack()  # seek-back -> re-read
    else:
        await msg.reject()  # commit/skip (poison; Waku DLQ is handled at the processing layer)


class _PausableConsumer(Protocol):
    """The slice of aiokafka's consumer the pause handle drives — partition-level pause/resume."""

    def assignment(self) -> Collection[TopicPartition]: ...
    def pause(self, *partitions: TopicPartition) -> None: ...
    def resume(self, *partitions: TopicPartition) -> None: ...


class KafkaSubscription(Subscription):
    """Pausable handle over one Kafka subscriber's live consumer.

    Toggles the live consumer at the partition level (not ``subscriber.stop()/start()``, which tears the consumer
    down and triggers a consumer-group rebalance). Idempotent via ``_paused``. The consumer is resolved fresh on
    each call because it is ``None`` before ``broker.start()`` / after stop.

    Decoupled from the FastStream subscriber via a consumer accessor so the pause logic is unit-testable without a
    live broker. Importable but not part of the module's public API (not in ``__all__``).
    """

    __slots__ = ('_get_consumer', '_paused')

    def __init__(self, get_consumer: Callable[[], _PausableConsumer | None]) -> None:
        self._get_consumer = get_consumer
        self._paused = False

    @override
    async def pause(self) -> None:
        if self._paused:
            return
        consumer = self._get_consumer()
        if consumer is not None:
            consumer.pause(*consumer.assignment())  # partition-level; no consumer-group rebalance
            self._paused = True

    @override
    async def resume(self) -> None:
        if not self._paused:
            return
        consumer = self._get_consumer()
        if consumer is not None:
            consumer.resume(*consumer.assignment())
            self._paused = False


class FastStreamKafkaTransport(ITransport):
    """Bidirectional Kafka transport over a single injected ``KafkaBroker``."""

    __slots__ = ('_auto_offset_reset', '_broker', '_consumer_group', '_started')

    def __init__(
        self,
        *,
        broker: KafkaBroker,
        consumer_group: str,
        auto_offset_reset: Literal['latest', 'earliest', 'none'] = 'latest',
    ) -> None:
        self._broker = broker
        self._consumer_group = consumer_group  # Kafka consumer group.id (competing consumers) — NOT the message key
        self._auto_offset_reset = auto_offset_reset
        self._started: bool = False

    @override
    async def send(self, body: dict[str, Any], *, destination: str, metadata: WireMetadata) -> None:
        # group_id -> Kafka message key (same key => same partition => ordering); None => round-robin.
        await self._broker.publish(body, destination, key=_key(metadata.group_id), headers=metadata.as_headers())

    @override
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> Subscription:
        # Capture the subscriber (instead of the decorator form) so it can be paused/resumed per-subscriber.
        subscriber = self._broker.subscriber(
            queue,
            group_id=self._consumer_group,
            ack_policy=AckPolicy.MANUAL,
            auto_offset_reset=self._auto_offset_reset,
        )

        async def _handler(msg: KafkaMessage) -> None:
            await dispatch_inbound(msg, on_message)

        subscriber(_handler)  # register the handler on the captured subscriber (KafkaSubscriber.__call__)
        return KafkaSubscription(lambda: subscriber.consumer)

    @override
    async def start(self) -> None:
        if not self._started:
            await self._broker.start()
            self._started = True

    @override
    async def stop(self) -> None:
        if self._started:
            await self._broker.stop()
            self._started = False


def kafka_transport(
    url: str,
    *,
    consumer_group: str,
    auto_offset_reset: Literal['latest', 'earliest', 'none'] = 'latest',
) -> TransportFactory:
    """Return a deferred factory for ``FastStreamKafkaTransport`` (not yet started).

    The framework invokes the factory once during DI bootstrap.

    Args:
        url: Kafka bootstrap servers (e.g. ``'localhost:9092'``).
        consumer_group: Kafka consumer ``group.id`` shared by all subscribers (competing consumers across
            pods) — distinct from the per-message ``group_id`` partition key.
        auto_offset_reset: Where a fresh consumer group starts reading.
    """

    # A closure (not functools.partial like rabbit_transport): the broker is injected, so each factory call
    # builds its own KafkaBroker — binding one eagerly via partial would share a single broker across calls.
    def _factory() -> ITransport:
        return FastStreamKafkaTransport(
            broker=KafkaBroker(url),
            consumer_group=consumer_group,
            auto_offset_reset=auto_offset_reset,
        )

    return _factory
