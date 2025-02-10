from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.mediator.contracts.event import EventT
    from waku.mediator.events.handler import EventHandler

__all__ = [
    'EventPublisher',
    'GroupEventPublisher',
    'SequentialEventPublisher',
]


class EventPublisher(Protocol):
    async def __call__(self, handlers: Sequence[EventHandler[EventT]], event: EventT) -> None:
        pass


class SequentialEventPublisher(EventPublisher):
    async def __call__(self, handlers: Sequence[EventHandler[EventT]], event: EventT) -> None:
        for handler in handlers:
            await handler.handle(event)


class GroupEventPublisher(EventPublisher):
    async def __call__(self, handlers: Sequence[EventHandler[EventT]], event: EventT) -> None:
        async with asyncio.TaskGroup() as tg:
            for handler in handlers:
                tg.create_task(handler.handle(event))
