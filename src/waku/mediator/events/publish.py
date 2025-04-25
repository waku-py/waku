from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import anyio

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
        async with anyio.create_task_group() as tg:
            for handler in handlers:
                tg.start_soon(handler.handle, event)
