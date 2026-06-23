"""RabbitMQ-specific FastStream inbound transport.

Broker-specific by design: the ``ConsumeDisposition`` -> ack/nack/reject mapping and the prefetch/QOS knob do not
generalise across brokers (e.g. Kafka ``reject()`` commits the offset, Redis ``nack()`` is a no-op,
``nack(requeue=True)`` is a RabbitMQ-only kwarg). Deliberately NOT re-exported from a generic package — a consumer
opts in by importing it from this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from faststream import AckPolicy
from faststream.rabbit import Channel
from faststream.rabbit.annotations import RabbitMessage  # noqa: TC002  -- runtime: fast_depends resolves via pydantic
from typing_extensions import override

from waku.messaging.transport.inbound import ConsumeDisposition, IInboundTransport

if TYPE_CHECKING:
    from faststream.rabbit import RabbitBroker

    from waku.messaging.transport.inbound import ConsumeCallback

__all__ = [
    'FastStreamRabbitInboundTransport',
]

logger = logging.getLogger(__name__)


class FastStreamRabbitInboundTransport(IInboundTransport):
    """RabbitMQ inbound transport: registers MANUAL-ack subscribers then activates them on ``start()``."""

    __slots__ = ('_broker', '_prefetch_count', '_started')

    def __init__(self, broker: RabbitBroker, *, prefetch_count: int = 250) -> None:
        self._broker = broker
        self._prefetch_count = prefetch_count  # bounds unacked messages under MANUAL ack; watermark tuning is Slice B
        self._started: bool = False

    @override
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> None:
        @self._broker.subscriber(
            queue,
            ack_policy=AckPolicy.MANUAL,
            channel=Channel(prefetch_count=self._prefetch_count),
            no_reply=True,
        )
        async def _handler(msg: RabbitMessage) -> None:
            # decode() runs before the try below: a malformed payload is poison (foreign format, out of Slice A
            # scope — Waku is producer+consumer here) and must not be requeued into a poison loop.
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

    @override
    async def start(self) -> None:
        if self._started:
            return
        await self._broker.start()
        self._started = True

    @override
    async def stop(self) -> None:
        if self._started:
            await self._broker.stop()
            self._started = False
