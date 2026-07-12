"""Dedicated consumer process: a Waku app configured only to consume from a broker.

Run it as its own pod/process. It owns the embedded FastStream broker, so it keeps full
Waku resilience. ``app.run()`` blocks until SIGTERM/SIGINT, then shuts down gracefully.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from typing_extensions import override

from waku import WakuFactory, module
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.messages import IEvent
from waku.messaging import EventHandler, InboxConfig, MessagingConfig, MessagingExtension, MessagingModule, listen
from waku.messaging.transport.faststream.rabbitmq import rabbit_transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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


def create_session() -> AsyncIterator[AsyncSession]:
    """Supply the consumer's scoped ``AsyncSession`` — the backend builds every durable store over it."""
    engine = create_async_engine('postgresql+psycopg://postgres:postgres@localhost:5432/postgres')
    return _session(engine)


async def _session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session  # noqa: ASYNC119


def build_config() -> MessagingConfig:
    """Consumer-only messaging config: a transport, an inbound listener, and an inbox (no outbox, no HTTP)."""
    return MessagingConfig(
        endpoints=[listen('rabbitmq://orders')],
        transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
        inbox=InboxConfig(),
    )


@module(
    imports=[
        MessagingModule.register(build_config()),
        SqlAlchemyBackend.register(session_factory=create_session),
    ],
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
