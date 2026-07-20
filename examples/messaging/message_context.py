"""Example demonstrating MessageContext for correlation and causation tracking.

This example shows:
1. How to access MessageContext inside handlers via get_message_context()
2. How correlation_id propagates across the message chain
3. How causation_id links parent and child messages
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.context import get_message_context

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PlaceOrder(IRequest[uuid.UUID]):
    item: str


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):
    order_id: uuid.UUID
    item: str


class PlaceOrderHandler(RequestHandler[PlaceOrder, uuid.UUID]):
    def __init__(self, bus: IMessageBus) -> None:
        self._bus = bus

    async def handle(self, request: PlaceOrder, /) -> uuid.UUID:
        ctx = get_message_context()
        order_id = uuid.uuid4()
        logger.info(
            '[PlaceOrderHandler] message_id=%s correlation_id=%s',
            ctx.message_id,
            ctx.correlation_id,
        )
        await self._bus.publish(OrderPlaced(order_id=order_id, item=request.item))
        return order_id


class OrderPlacedHandler(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        ctx = get_message_context()
        # correlation_id propagates through the chain
        logger.info(
            '[OrderPlacedHandler] message_id=%s correlation_id=%s causation_id=%s',
            ctx.message_id,
            ctx.correlation_id,
            ctx.causation_id,
        )


@module(
    extensions=[
        (MessagingExtension().bind(PlaceOrder, PlaceOrderHandler).bind(OrderPlaced, OrderPlacedHandler)),
    ],
)
class OrderModule: ...


@module(imports=[OrderModule, MessagingModule.register()])
class AppModule: ...


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)
        order_id = await bus.invoke(PlaceOrder(item='keyboard'))
        logger.info('[main] order created: %s', order_id)


if __name__ == '__main__':
    asyncio.run(main())
