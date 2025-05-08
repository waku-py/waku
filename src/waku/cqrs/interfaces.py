from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waku.cqrs.contracts.event import Event
    from waku.cqrs.contracts.request import Request, ResponseT

__all__ = [
    'IMediator',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send a request through the cqrs middleware chain to be handled by a single handler."""

    @abc.abstractmethod
    async def send(self, request: Request[ResponseT]) -> ResponseT:
        """Asynchronously send a request to a single handler."""


class IPublisher(abc.ABC):
    """Publish event through the cqrs to be handled by multiple handlers."""

    @abc.abstractmethod
    async def publish(self, event: Event) -> None:
        """Asynchronously send event to multiple handlers."""


class IMediator(ISender, IPublisher, abc.ABC):
    """Defines a cqrs to encapsulate request/response and publishing interaction patterns."""
