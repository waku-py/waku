"""Dedicated consumer process: a Waku app configured only to consume from a broker.

Run it as its own pod/process. It owns the embedded FastStream broker, so it keeps full
Waku resilience. ``app.run()`` blocks until SIGTERM/SIGINT, then shuts down gracefully.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.messaging import (
    EventHandler,
    IEvent,
    IInboxStore,
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    listen,
)
from waku.messaging.transport.faststream.rabbitmq import rabbit_transport


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    """Event consumed from the ``orders`` queue."""

    order_id: str


class OrderPlacedHandler(EventHandler[OrderPlaced]):
    """Handles each consumed order."""

    @override
    async def handle(self, message: OrderPlaced, /) -> None:
        """Process a consumed ``OrderPlaced`` event."""
        print(f'handling order {message.order_id}')


def build_inbox_store() -> IInboxStore:
    """Supply your ``IInboxStore`` here — e.g. the SQLAlchemy adapter wired to your session factory."""
    msg = 'Supply your IInboxStore — e.g. the SQLAlchemy adapter wired to a session factory.'
    raise NotImplementedError(msg)


def build_config() -> MessagingConfig:
    """Consumer-only messaging config: a transport, an inbound listener, and an inbox (no outbox, no HTTP)."""
    return MessagingConfig(
        endpoints=[listen('rabbitmq://orders')],
        transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
        inbox=InboxConfig(store=build_inbox_store),
    )


@module(
    imports=[MessagingModule.register(build_config())],
    extensions=[MessagingExtension().bind(OrderPlacedHandler)],
)
class ConsumerModule:
    """Root module for the dedicated consumer process."""


async def main() -> None:
    """Boot the consumer, then block until SIGTERM/SIGINT and drain + close the broker."""
    app = WakuFactory(ConsumerModule).create()
    await app.run()  # blocks until SIGTERM/SIGINT, then drains + closes the broker


if __name__ == '__main__':
    asyncio.run(main())


# A producer elsewhere publishes OrderPlaced to the 'orders' queue; this process consumes it.
# Scale out by running N identical consumer pods — RabbitMQ competing consumers distribute the
# work and the inbox (id, handler) dedup absorbs any duplicate delivery (no leader election).
