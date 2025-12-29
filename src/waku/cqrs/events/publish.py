from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import anyio

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.cqrs.contracts.notification import NotificationT
    from waku.cqrs.events.handler import INotificationHandler

__all__ = [
    'EventPublisher',
    'GroupEventPublisher',
    'SequentialEventPublisher',
]


class EventPublisher(Protocol):
    async def __call__(
        self, handlers: Sequence[INotificationHandler[NotificationT]], event: NotificationT, /
    ) -> None: ...


class SequentialEventPublisher(EventPublisher):
    async def __call__(self, handlers: Sequence[INotificationHandler[NotificationT]], event: NotificationT, /) -> None:
        for handler in handlers:
            await handler.handle(event)


class GroupEventPublisher(EventPublisher):
    async def __call__(self, handlers: Sequence[INotificationHandler[NotificationT]], event: NotificationT, /) -> None:
        async with anyio.create_task_group() as tg:
            for handler in handlers:
                tg.start_soon(handler.handle, event)
