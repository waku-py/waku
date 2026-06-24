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
import logging
from typing import TYPE_CHECKING, Any

from faststream import AckPolicy
from faststream.rabbit import Channel, RabbitBroker
from faststream.rabbit.annotations import RabbitMessage  # noqa: TC002  -- runtime: fast_depends resolves via pydantic
from typing_extensions import override

from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import ITransport, Subscription

if TYPE_CHECKING:
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import TransportFactory, WireMetadata

__all__ = [
    'FastStreamRabbitTransport',
    'rabbit_transport',
]

logger = logging.getLogger(__name__)


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


class FastStreamRabbitTransport(ITransport):
    """Bidirectional RabbitMQ transport with dedicated send and listen broker connections."""

    __slots__ = ('_listen_broker', '_prefetch_count', '_send_broker', '_started')

    def __init__(self, *, url: str, prefetch_count: int = 250) -> None:
        self._send_broker = RabbitBroker(url)
        self._listen_broker = RabbitBroker(url)
        self._prefetch_count = prefetch_count  # bounds unacked messages under MANUAL ack
        self._started: bool = False

    @override
    async def send(self, body: dict[str, Any], *, destination: str, metadata: WireMetadata) -> None:
        # Widen str-valued correlation headers to the broker's field-value type at publish.
        headers: dict[str, Any] = metadata.as_headers()
        await self._send_broker.publish(  # pyrefly: ignore[unexpected-keyword]
            body,
            destination,
            headers=headers,  # pyrefly: ignore[unexpected-keyword]
        )

    @override
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> Subscription:
        # Capture the subscriber (instead of the decorator form) so it can be paused/resumed per-subscriber.
        subscriber = self._listen_broker.subscriber(
            queue,
            ack_policy=AckPolicy.MANUAL,
            channel=Channel(prefetch_count=self._prefetch_count),
            no_reply=True,
        )

        async def _handler(msg: RabbitMessage) -> None:
            # decode() runs before the try below: a malformed payload is poison (a foreign wire format — Waku
            # is both producer and consumer here) and must not be requeued into a poison loop.
            body: dict[str, Any] = await msg.decode()  # type: ignore[assignment]
            try:
                disposition = await on_message(body)
            except Exception:
                # MANUAL ack: a raised handler would leave the message unacked — requeue for redelivery.
                logger.exception('Inbound message handling failed; requeueing message')
                await msg.nack(requeue=True)
                return
            if disposition is ConsumeDisposition.ACK:
                await msg.ack()
            elif disposition is ConsumeDisposition.NACK_REQUEUE:
                await msg.nack(requeue=True)
            else:
                await msg.reject()

        subscriber(_handler)  # register the handler on the captured subscriber (RabbitSubscriber.__call__)
        return _FastStreamSubscription(subscriber)

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


def rabbit_transport(url: str, *, prefetch_count: int = 250) -> TransportFactory:
    """Return a deferred factory for ``FastStreamRabbitTransport`` (not yet started).

    The framework invokes the factory once during DI bootstrap.

    Args:
        url: AMQP connection URL (e.g. ``'amqp://guest:guest@localhost/'``).
        prefetch_count: Maximum unacknowledged messages per consumer channel.
    """
    return functools.partial(FastStreamRabbitTransport, url=url, prefetch_count=prefetch_count)
