"""Kafka-specific FastStream transport (aiokafka backend).

Broker-specific by design: the ``ConsumeDisposition`` -> ack/nack/reject mapping and the partition-key/commit
model do not generalise across brokers (e.g. Kafka ``reject()`` commits the offset, Kafka has no broker requeue).
Deliberately NOT re-exported from a generic package — a consumer opts in by importing it from this module.

One ``KafkaBroker`` (injected): Kafka's producer and consumer are already separate clients with separate
connections and no shared connection-level flow control, so the Rabbit two-connection isolation is unnecessary.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.kafka.annotations import KafkaMessage  # runtime: Generic base subscript + fast_depends DI
from typing_extensions import override

from waku.messaging.transport.faststream.base import FastStreamTransportBase
from waku.messaging.transport.interfaces import IEnvelopeMapper, ITransport, Subscription
from waku.messaging.transport.mapping import metadata_from_headers, wire_headers_of

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from aiokafka.structs import TopicPartition

    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import EnvelopeMetadata, TransportFactory

__all__ = [
    'DefaultKafkaEnvelopeMapper',
    'FastStreamKafkaTransport',
    'IKafkaEnvelopeMapper',
    'KafkaOutgoing',
    'kafka_transport',
]


def _key(group_id: str | None) -> bytes | None:
    return group_id.encode('utf-8') if group_id is not None else None


@dataclass(frozen=True, slots=True, kw_only=True)
class KafkaOutgoing:
    """Outgoing Kafka message ready for ``KafkaBroker.publish``.

    ``body``, ``key``, and ``headers`` are always passed to ``KafkaBroker.publish``.
    The native fields below are **passthrough-only** — the default ``DefaultKafkaEnvelopeMapper`` leaves them
    ``None`` and they are omitted from the publish call.  A custom mapper may set them to reach aiokafka
    capabilities that the Wolverine wire format does not expose.

    Native fields (verified against FastStream 0.7.1 ``KafkaBroker.publish`` signature):
        partition: Target partition override; ``None`` lets the partitioner decide.
        timestamp_ms: Message timestamp in milliseconds since epoch; ``None`` uses the broker default.
        correlation_id: FastStream request-reply correlation id; distinct from the Waku envelope
            ``correlation_id`` header — do not confuse the two.
        reply_to: FastStream reply topic; ``None`` (or empty string) disables request-reply.
    """

    body: dict[str, Any]
    key: bytes | None
    headers: dict[str, str]
    partition: int | None = None
    timestamp_ms: int | None = None
    correlation_id: str | None = None
    reply_to: str | None = None


class IKafkaEnvelopeMapper(IEnvelopeMapper['KafkaMessage', KafkaOutgoing]):
    """Per-Kafka-broker envelope mapper: owns the wire format for both directions."""


class DefaultKafkaEnvelopeMapper(IKafkaEnvelopeMapper):
    """Wolverine-faithful Kafka envelope mapper.

    Outgoing: payload-in-body, Wolverine headers (bare + skip-if-reserved), ``group_id`` → Kafka message KEY.
    Incoming: decode body, reconstruct ``EnvelopeMetadata`` from headers; Kafka message key takes precedence
    over any ``group_id`` header (key-takes-precedence Wolverine rule).
    """

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> KafkaOutgoing:
        return KafkaOutgoing(
            body=payload,
            key=_key(metadata.group_id),
            headers=wire_headers_of(metadata),
        )

    @override
    async def map_incoming(self, msg: KafkaMessage) -> tuple[dict[str, Any], EnvelopeMetadata]:
        payload = cast('dict[str, Any]', await msg.decode())
        meta = metadata_from_headers(msg.headers)
        # Kafka message key takes precedence over the group_id header (Wolverine key-takes-precedence rule).
        raw_message = cast('Any', msg.raw_message)  # raw_message not typed on KafkaMessage; pyrefly: ignore
        raw_key: bytes | None = raw_message.key
        # Truthy guard: an empty-bytes key ``b''`` is treated as no-key so the header group_id is kept.
        # Real aiokafka yields None for keyless messages; this hardens the field against empty-bytes edge cases.
        if raw_key:
            meta = dataclasses.replace(meta, group_id=raw_key.decode())
        return payload, meta


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


class FastStreamKafkaTransport(FastStreamTransportBase[KafkaMessage]):
    """Bidirectional Kafka transport over a single injected ``KafkaBroker``."""

    __slots__ = ('_auto_offset_reset', '_broker', '_consumer_group', '_mapper', '_started')

    def __init__(
        self,
        *,
        broker: KafkaBroker,
        consumer_group: str,
        auto_offset_reset: Literal['latest', 'earliest', 'none'] = 'latest',
        mapper: IKafkaEnvelopeMapper | None = None,
    ) -> None:
        self._broker = broker
        self._consumer_group = consumer_group  # Kafka consumer group.id (competing consumers) — NOT the message key
        self._auto_offset_reset = auto_offset_reset
        self._mapper: IKafkaEnvelopeMapper = mapper or DefaultKafkaEnvelopeMapper()
        self._started: bool = False

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        effective = mapper or self._mapper
        out = effective.map_outgoing(body, metadata)
        extra: dict[str, Any] = {}
        if out.partition is not None:
            extra['partition'] = out.partition
        if out.timestamp_ms is not None:
            extra['timestamp_ms'] = out.timestamp_ms
        if out.correlation_id is not None:
            extra['correlation_id'] = out.correlation_id
        if out.reply_to is not None:
            extra['reply_to'] = out.reply_to
        await self._broker.publish(out.body, destination, key=out.key, headers=out.headers, **extra)  # pyrefly: ignore[unexpected-keyword]

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        # Capture the subscriber (instead of the decorator form) so it can be paused/resumed per-subscriber.
        subscriber = self._broker.subscriber(
            queue,
            group_id=self._consumer_group,
            ack_policy=AckPolicy.MANUAL,
            auto_offset_reset=self._auto_offset_reset,
        )

        effective = mapper or self._mapper

        async def _handler(msg: KafkaMessage) -> None:
            await self._dispatch_inbound(msg, on_message, effective)

        subscriber(_handler)  # register the handler on the captured subscriber (KafkaSubscriber.__call__)
        return KafkaSubscription(lambda: subscriber.consumer)

    @override
    async def _ack(self, msg: KafkaMessage) -> None:
        await msg.ack()  # commit offset

    @override
    async def _nack(self, msg: KafkaMessage) -> None:
        await msg.nack()  # seek-back -> re-read on next poll (Kafka has no broker requeue)

    @override
    async def _reject(self, msg: KafkaMessage) -> None:
        await msg.reject()  # commit/skip (poison; Waku DLQ is handled at the processing layer)

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
    mapper: IKafkaEnvelopeMapper | None = None,
) -> TransportFactory:
    """Return a deferred factory for ``FastStreamKafkaTransport`` (not yet started).

    The framework invokes the factory once during DI bootstrap.

    Args:
        url: Kafka bootstrap servers (e.g. ``'localhost:9092'``).
        consumer_group: Kafka consumer ``group.id`` shared by all subscribers (competing consumers across
            pods) — distinct from the per-message ``group_id`` partition key.
        auto_offset_reset: Where a fresh consumer group starts reading.
        mapper: Envelope mapper; defaults to ``DefaultKafkaEnvelopeMapper``.
    """

    # A closure (not functools.partial like rabbit_transport): the broker is injected, so each factory call
    # builds its own KafkaBroker — binding one eagerly via partial would share a single broker across calls.
    def _factory() -> ITransport:
        return FastStreamKafkaTransport(
            broker=KafkaBroker(url),
            consumer_group=consumer_group,
            auto_offset_reset=auto_offset_reset,
            mapper=mapper,
        )

    return _factory
