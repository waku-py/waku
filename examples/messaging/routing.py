"""Example demonstrating endpoint routing with local queues.

This example shows:
1. How to configure local queue endpoints for async processing
2. How to use route_module() for module-level event routing
3. How to use route() for per-type routing overrides
4. How inline and queued handlers work together (additive routing)
5. How send() provides fire-and-forget dispatch (routable, unlike invoke())
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from waku import WakuFactory, module
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.router import local_queue, route, route_module

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


# --- Messages ---


@dataclass(frozen=True, kw_only=True)
class PlaceOrder(IRequest[str]):
    item: str


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):
    order_id: str
    item: str


@dataclass(frozen=True, kw_only=True)
class ShipOrder(IRequest[None]):
    order_id: str


@dataclass(frozen=True, kw_only=True)
class HighPriorityAlert(IEvent):
    order_id: str
    reason: str


# --- Order module (inline handlers) ---


class PlaceOrderHandler(RequestHandler[PlaceOrder, str]):
    def __init__(self, bus: IMessageBus) -> None:
        self._bus = bus

    async def handle(self, request: PlaceOrder, /) -> str:
        order_id = f'ORD-{request.item[:3].upper()}'
        logger.info('[OrderModule] order placed: %s', order_id)
        await self._bus.publish(OrderPlaced(order_id=order_id, item=request.item))
        await self._bus.publish(HighPriorityAlert(order_id=order_id, reason='high-value item'))
        return order_id


class ShipOrderHandler(RequestHandler[ShipOrder, None]):
    """Queued handler — routed via send(), processed in the background."""

    async def handle(self, request: ShipOrder, /) -> None:
        logger.info('[OrderModule] shipping order %s', request.order_id)


class OrderAuditHandler(EventHandler[OrderPlaced]):
    """Inline handler — runs in the caller's context, not via the queue."""

    async def handle(self, event: OrderPlaced, /) -> None:
        logger.info('[OrderModule] audit log: order %s', event.order_id)


@module(
    extensions=[
        (
            MessagingExtension()
            .bind(PlaceOrder, PlaceOrderHandler)
            .bind(ShipOrder, ShipOrderHandler)
            .bind(OrderPlaced, OrderAuditHandler)
        ),
    ],
)
class OrderModule: ...


# --- Notification module (routed to local queue) ---


class OrderNotificationHandler(EventHandler[OrderPlaced]):
    """Queued handler — runs asynchronously in the local queue worker."""

    async def handle(self, event: OrderPlaced, /) -> None:
        logger.info('[NotificationModule] sending notification for order %s', event.order_id)


class HighPriorityAlertHandler(EventHandler[HighPriorityAlert]):
    """Queued handler — routed to the priority queue via per-type override."""

    async def handle(self, event: HighPriorityAlert, /) -> None:
        logger.info('[NotificationModule] PRIORITY alert for order %s: %s', event.order_id, event.reason)


@module(
    extensions=[
        (
            MessagingExtension()
            .bind(OrderPlaced, OrderNotificationHandler)
            .bind(HighPriorityAlert, HighPriorityAlertHandler)
        ),
    ],
)
class NotificationModule: ...


# --- App wiring ---


@module(
    imports=[
        OrderModule,
        NotificationModule,
        MessagingModule.register(
            MessagingConfig(
                endpoints=[
                    local_queue('notifications'),
                    local_queue('priority'),
                ],
                routing=[
                    # All events from NotificationModule are processed via the 'notifications' queue.
                    # OrderModule's handlers still run inline (additive routing).
                    route_module(NotificationModule).to('notifications'),
                    # Per-type override: HighPriorityAlert goes to 'priority' queue
                    # instead of 'notifications', even though it belongs to NotificationModule.
                    route(HighPriorityAlert).to('priority'),
                    # send() is fire-and-forget — route the command to a background queue.
                    route(ShipOrder).to('notifications'),
                ],
            ),
        ),
    ],
)
class AppModule: ...


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)
        # invoke() — always inline, returns a typed response
        order_id = await bus.invoke(PlaceOrder(item='keyboard'))
        logger.info('[main] order created: %s', order_id)

        # send() — fire-and-forget, routed to 'notifications' queue
        await bus.send(ShipOrder(order_id=order_id))

        # Give the queue workers time to process
        await asyncio.sleep(0.2)


if __name__ == '__main__':
    asyncio.run(main())
