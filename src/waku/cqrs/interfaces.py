from __future__ import annotations

import abc
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.cqrs.contracts.request import IRequest, ResponseT

__all__ = [
    'IMediator',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send a request through the cqrs middleware chain to be handled by a single handler."""

    @overload
    async def send(self, request: IRequest[None], /) -> None: ...

    @overload
    async def send(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @abc.abstractmethod
    async def send(self, request: IRequest[ResponseT], /) -> ResponseT:
        """Asynchronously send a request to a single handler."""


class IPublisher(abc.ABC):
    """Publish notification through the cqrs to be handled by multiple handlers."""

    @abc.abstractmethod
    async def publish(self, notification: INotification, /) -> None:
        """Asynchronously send notification to multiple handlers."""


class IMediator(ISender, IPublisher, abc.ABC):
    """Defines a cqrs to encapsulate request/response and publishing interaction patterns."""
