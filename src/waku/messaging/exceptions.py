from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.exceptions import WakuError

if TYPE_CHECKING:
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.events.handler import EventHandler
    from waku.messaging.requests.handler import RequestHandler

__all__ = [
    'EventHandlerAlreadyRegistered',
    'ImproperlyConfiguredError',
    'MapFrozenError',
    'MessagingError',
    'PipelineBehaviorAlreadyRegistered',
    'RequestHandlerAlreadyRegistered',
    'RequestHandlerNotFound',
]


class MessagingError(WakuError):
    """Base exception for all messaging-related errors."""


class MapFrozenError(MessagingError):
    def __init__(self) -> None:
        super().__init__('Cannot modify map after it is frozen')


class ImproperlyConfiguredError(MessagingError):
    """Raised when messaging configuration is invalid."""


class RequestHandlerAlreadyRegistered(MessagingError, KeyError):  # noqa: N818
    """Raised when a request handler is already registered.

    Attributes:
        request_type: The type of request that caused the error.
        handler_type: The type of handler that was already registered.
    """

    def __init__(self, request_type: type[IRequest[Any]], handler_type: type[RequestHandler[Any, Any]]) -> None:
        self.request_type = request_type
        self.handler_type = handler_type

    def __str__(self) -> str:
        return f'{self.request_type.__name__} already exists in registry with handler {self.handler_type.__name__}'


class RequestHandlerNotFound(MessagingError, TypeError):  # noqa: N818
    """Raised when a request handler is not found.

    Attributes:
        request_type: The type of request that caused the error.
    """

    def __init__(self, request_type: type[IRequest[Any]]) -> None:
        self.request_type = request_type

    def __str__(self) -> str:
        return f'Request handler for {self.request_type.__name__} request is not registered'


class EventHandlerAlreadyRegistered(MessagingError, KeyError):  # noqa: N818
    """Raised when an event handler is already registered.

    Attributes:
        event_type: The type of event that caused the error.
        handler_type: The type of handler that was already registered.
    """

    def __init__(self, event_type: type[IEvent], handler_type: type[EventHandler[Any]]) -> None:
        self.event_type = event_type
        self.handler_type = handler_type

    def __str__(self) -> str:
        return f'{self.handler_type.__name__} already registered for {self.event_type.__name__} event'


class PipelineBehaviorAlreadyRegistered(MessagingError, KeyError):  # noqa: N818
    """Raised when a pipeline behavior is already registered.

    Attributes:
        request_type: The type of request that caused the error.
        behavior_type: The type of behavior that was already registered.
    """

    def __init__(self, request_type: type[IRequest[Any]], behavior_type: type[IPipelineBehavior[Any, Any]]) -> None:
        self.request_type = request_type
        self.behavior_type = behavior_type

    def __str__(self) -> str:
        return f'{self.behavior_type.__name__} already registered for {self.request_type.__name__} request'
