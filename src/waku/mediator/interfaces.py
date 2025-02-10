import abc

from waku.mediator.contracts.event import Event
from waku.mediator.contracts.request import Request, ResponseT

__all__ = [
    'IMediator',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send a request through the mediator middleware chain to be handled by a single handler."""

    @abc.abstractmethod
    async def send(self, request: Request[ResponseT]) -> ResponseT:
        """Asynchronously send a request to a single handler."""


class IPublisher(abc.ABC):
    """Publish event through the mediator to be handled by multiple handlers."""

    @abc.abstractmethod
    async def publish(self, event: Event) -> None:
        """Asynchronously send event to multiple handlers."""


class IMediator(ISender, IPublisher, abc.ABC):
    """Defines a mediator to encapsulate request/response and publishing interaction patterns."""
