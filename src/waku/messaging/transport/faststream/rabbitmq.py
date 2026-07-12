"""RabbitMQ-specific FastStream bidirectional transport.

Broker-specific by design: the ``ConsumeDisposition`` -> ack/nack/reject mapping and the prefetch/QOS knob do not
generalise across brokers (e.g. Kafka ``reject()`` commits the offset, Redis ``nack()`` is a no-op,
``nack(requeue=True)`` is a RabbitMQ-only kwarg). Deliberately NOT re-exported from a generic package — a consumer
opts in by importing it from this module.

Two separate ``RabbitBroker`` connections are used: one exclusively for publishing (``_send_broker``) and one for
consuming (``_listen_broker``).  This isolation prevents head-of-line blocking between producers and consumers sharing
a single AMQP channel pool, matches the Wolverine two-connection model, and keeps prefetch-count semantics correct.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from faststream import AckPolicy
from faststream.rabbit import Channel, RabbitBroker
from faststream.rabbit.annotations import RabbitMessage  # runtime: Generic base subscript + fast_depends DI
from typing_extensions import override

from waku.messaging.transport.faststream.base import FastStreamTransportBase
from waku.messaging.transport.interfaces import IEnvelopeMapper, Subscription
from waku.messaging.transport.mapping import metadata_from_headers, wire_headers_of

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import EnvelopeMetadata, TransportFactory

__all__ = [
    'DefaultRabbitEnvelopeMapper',
    'FastStreamRabbitTransport',
    'IRabbitEnvelopeMapper',
    'RabbitOutgoing',
    'rabbit_transport',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class RabbitOutgoing:
    """Outgoing RabbitMQ message ready for ``RabbitBroker.publish``.

    ``body``, ``headers``, and ``persist`` are always passed to ``RabbitBroker.publish``.
    The native fields below are **passthrough-only** — the default ``DefaultRabbitEnvelopeMapper`` leaves them
    ``None`` and they are omitted from the publish call.  A custom mapper may set them to reach AMQP message
    properties that the Wolverine wire format does not expose.

    ``persist`` is **not** a passthrough native: it is the durability guarantee, on by default.  Every Waku
    broker send is outbox-backed, so publishes are ``DeliveryMode.PERSISTENT`` unless a custom mapper opts out
    with ``persist=False`` (foreign interop / non-durable topics).

    Native fields (verified against FastStream 0.7.1 ``RabbitBroker.publish`` signature):
        correlation_id: AMQP ``correlation-id`` property.  Distinct from the Waku envelope ``correlation_id``
            header — do not confuse the two.
        reply_to: AMQP ``reply-to`` property (routing key for reply messages; always uses the default exchange).
        priority: AMQP ``priority`` property (0–255); ``None`` lets the broker use its default (0).
        expiration: AMQP message expiration.  Accepts ``int`` or ``float`` (seconds), ``datetime``, or
            ``timedelta`` — as forwarded by aio_pika's ``DateType``.
    """

    body: dict[str, Any]
    headers: dict[str, str]
    persist: bool = True  # AMQP delivery mode: True -> PERSISTENT (survives broker restart in a durable queue)
    correlation_id: str | None = None
    reply_to: str | None = None
    priority: int | None = None
    expiration: int | float | datetime | timedelta | None = None


class IRabbitEnvelopeMapper(IEnvelopeMapper['RabbitMessage', RabbitOutgoing]):
    """Per-RabbitMQ-broker envelope mapper: owns the wire format for both directions."""


class DefaultRabbitEnvelopeMapper(IRabbitEnvelopeMapper):
    """Wolverine-faithful RabbitMQ envelope mapper.

    Outgoing: payload-in-body, Wolverine headers (bare + skip-if-reserved); ``group_id`` rides as a header
    (RabbitMQ has no message key concept).
    Incoming: decode body, reconstruct ``EnvelopeMetadata`` from headers; ``group_id`` comes from the header.
    """

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
        return RabbitOutgoing(
            body=payload,
            headers=wire_headers_of(metadata),
        )

    @override
    async def map_incoming(self, msg: RabbitMessage) -> tuple[dict[str, Any], EnvelopeMetadata]:
        payload = cast('dict[str, Any]', await msg.decode())
        meta = metadata_from_headers(msg.headers)
        return payload, meta


class _FastStreamSubscription(Subscription):
    """Wraps one ``RabbitSubscriber`` as a pausable handle.

    ``pause()`` issues ``basic.cancel`` (draining in-flight via FastStream's ``MultiLock``); ``resume()`` re-issues
    ``basic.consume`` on the live channel. FastStream has no double-start/stop guard, so a ``_running`` flag keeps both
    idempotent.
    """

    __slots__ = ('_running', '_subscriber')

    def __init__(self, subscriber: Any) -> None:
        self._subscriber = subscriber
        self._running = True  # FastStream activates the subscriber at broker.start()

    @override
    async def pause(self) -> None:
        if self._running:
            await self._subscriber.stop()
            self._running = False

    @override
    async def resume(self) -> None:
        if not self._running:
            await self._subscriber.start()
            self._running = True


class FastStreamRabbitTransport(FastStreamTransportBase[RabbitMessage]):
    """Bidirectional RabbitMQ transport with dedicated send and listen broker connections."""

    __slots__ = ('_listen_broker', '_mapper', '_prefetch_count', '_send_broker', '_started')

    def __init__(
        self,
        *,
        url: str,
        prefetch_count: int = 250,
        mapper: IRabbitEnvelopeMapper | None = None,
    ) -> None:
        self._send_broker = RabbitBroker(url)
        self._listen_broker = RabbitBroker(url)
        self._prefetch_count = prefetch_count  # bounds unacked messages under MANUAL ack
        self._mapper: IRabbitEnvelopeMapper = mapper or DefaultRabbitEnvelopeMapper()
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
        # Widen str-valued headers to the broker's FieldValue type at publish.
        headers: dict[str, Any] = out.headers
        extra: dict[str, Any] = {}
        if out.correlation_id is not None:
            extra['correlation_id'] = out.correlation_id
        if out.reply_to is not None:
            extra['reply_to'] = out.reply_to
        if out.priority is not None:
            extra['priority'] = out.priority
        if out.expiration is not None:
            extra['expiration'] = out.expiration
        await self._send_broker.publish(  # pyrefly: ignore[unexpected-keyword]
            out.body,
            destination,
            headers=headers,
            persist=out.persist,
            **extra,
        )

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        # Capture the subscriber (instead of the decorator form) so it can be paused/resumed per-subscriber.
        subscriber = self._listen_broker.subscriber(
            queue,
            ack_policy=AckPolicy.MANUAL,
            channel=Channel(prefetch_count=self._prefetch_count),
            no_reply=True,
        )

        effective = mapper or self._mapper

        async def _handler(msg: RabbitMessage) -> None:
            await self._dispatch_inbound(msg, on_message, effective)

        subscriber(_handler)  # register the handler on the captured subscriber (RabbitSubscriber.__call__)
        return _FastStreamSubscription(subscriber)

    @override
    async def _ack(self, msg: RabbitMessage) -> None:
        await msg.ack()

    @override
    async def _nack(self, msg: RabbitMessage) -> None:
        await msg.nack(requeue=True)  # MANUAL ack: requeue for redelivery (RabbitMQ-only kwarg)

    @override
    async def _reject(self, msg: RabbitMessage) -> None:
        await msg.reject()  # no requeue -> DLX/drop (poison; Waku DLQ is handled at the processing layer)

    @override
    async def start(self) -> None:
        if self._started:
            return
        # Send broker must be started first: publish raises IncorrectState if producer is unconnected.
        await self._send_broker.start()
        await self._listen_broker.start()
        self._started = True

    @override
    async def stop(self) -> None:
        if self._started:
            # Drain consumers before closing the publish connection.
            await self._listen_broker.stop()
            await self._send_broker.stop()
            self._started = False


def rabbit_transport(
    url: str,
    *,
    prefetch_count: int = 250,
    mapper: IRabbitEnvelopeMapper | None = None,
) -> TransportFactory:
    """Return a deferred factory for ``FastStreamRabbitTransport`` (not yet started).

    The framework invokes the factory once during DI bootstrap.

    Args:
        url: AMQP connection URL (e.g. ``'amqp://guest:guest@localhost/'``).
        prefetch_count: Maximum unacknowledged messages per consumer channel.
        mapper: Envelope mapper; defaults to ``DefaultRabbitEnvelopeMapper``.
    """
    return functools.partial(FastStreamRabbitTransport, url=url, prefetch_count=prefetch_count, mapper=mapper)
