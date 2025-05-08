from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.exceptions import WakuError

if TYPE_CHECKING:
    from waku.cqrs.contracts.event import Event, EventT
    from waku.cqrs.contracts.pipeline import IPipelineBehavior
    from waku.cqrs.contracts.request import Request
    from waku.cqrs.events.handler import EventHandlerType
    from waku.cqrs.requests.handler import RequestHandlerType

__all__ = [
    'EventHandlerAlreadyRegistered',
    'EventHandlerNotFound',
    'ImproperlyConfiguredError',
    'MediatorError',
    'PipelineBehaviorAlreadyRegistered',
    'RequestHandlerAlreadyRegistered',
    'RequestHandlerNotFound',
]


class MediatorError(WakuError):
    """Base exception for all cqrs-related errors."""


class ImproperlyConfiguredError(MediatorError):
    """Raised when cqrs configuration is invalid."""


class RequestHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    """Raised when a request handler is already registered.

    Attributes:
        request_type: The type of request that caused the error.
        handler_type: The type of handler that was already registered.
    """

    def __init__(self, request_type: type[Request[Any]], handler_type: RequestHandlerType[Any, Any]) -> None:
        self.request_type = request_type
        self.handler_type = handler_type

    def __str__(self) -> str:
        return f'{self.request_type.__name__} already exists in registry with handler {self.handler_type.__name__}'


class RequestHandlerNotFound(MediatorError, TypeError):  # noqa: N818
    """Raised when a request handler is not found.

    Attributes:
        request_type: The type of request that caused the error.
    """

    def __init__(self, request_type: type[Request[Any]]) -> None:
        self.request_type = request_type

    def __str__(self) -> str:
        return f'Request handler for {self.request_type.__name__} request is not registered'


class EventHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    """Raised when an event handler is already registered.

    Attributes:
        event_type: The type of event that caused the error.
        handler_type: The type of handler that was already registered.
    """

    def __init__(self, event_type: type[EventT], handler_type: EventHandlerType[EventT]) -> None:
        self.event_type = event_type
        self.handler_type = handler_type

    def __str__(self) -> str:
        return f'{self.handler_type.__name__} already registered for {self.event_type.__name__} event'


class EventHandlerNotFound(MediatorError, TypeError):  # noqa: N818
    """Raised when an event handler is not found.

    Attributes:
        event_type: The type of event that caused the error.
    """

    def __init__(self, event_type: type[Event]) -> None:
        self.event_type = event_type

    def __str__(self) -> str:
        return f'Event handlers for {self.event_type.__name__} event is not registered'


class PipelineBehaviorAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    """Raised when a pipeline behavior is already registered.

    Attributes:
        request_type: The type of request that caused the error.
        behavior_type: The type of behavior that was already registered.
    """

    def __init__(self, request_type: type[Request], behavior_type: type[IPipelineBehavior[Any, Any]]) -> None:
        self.request_type = request_type
        self.behavior_type = behavior_type

    def __str__(self) -> str:
        return f'{self.behavior_type.__name__} already registered for {self.request_type.__name__} request'
