from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.mediator.contracts.event import Event
    from waku.mediator.contracts.request import Request
    from waku.mediator.events.handler import EventHandlerType
    from waku.mediator.requests.handler import RequestHandlerType

__all__ = [
    'EventHandlerAlreadyRegistered',
    'EventHandlerNotFound',
    'ImproperlyConfiguredError',
    'MediatorError',
    'RequestHandlerAlreadyRegistered',
    'RequestHandlerNotFound',
]


class MediatorError(Exception):
    """Base exception for all mediator-related errors."""


class ImproperlyConfiguredError(MediatorError):
    """Raised when mediator configuration is invalid."""


class RequestHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    """Raised when a request handler is already registered.

    Attributes:
        request_type: The type of request that caused the error.
        handler_type: The type of handler that was already registered.
    """

    request_type: type[Request[Any]]
    handler_type: RequestHandlerType[Any, Any]

    def __init__(
        self,
        msg: str,
        request_type: type[Request[Any]],
        handler_type: RequestHandlerType[Any, Any],
    ) -> None:
        super().__init__(msg)
        self.request_type = request_type
        self.handler_type = handler_type


class RequestHandlerNotFound(MediatorError, TypeError):  # noqa: N818
    """Raised when a request handler is not found.

    Attributes:
        request_type: The type of request that caused the error.
    """

    request_type: type[Request[Any]]

    def __init__(self, msg: str, request_type: type[Request[Any]]) -> None:
        super().__init__(msg)
        self.request_type = request_type


class EventHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    """Raised when an event handler is already registered.

    Attributes:
        event_type: The type of event that caused the error.
        handler_type: The type of handler that was already registered.
    """

    event_type: type[Event]
    handler_type: EventHandlerType[Any]

    def __init__(
        self,
        msg: str,
        event_type: type[Event],
        handler_type: EventHandlerType[Any],
    ) -> None:
        super().__init__(msg)
        self.event_type = event_type
        self.handler_type = handler_type


class EventHandlerNotFound(MediatorError, TypeError):  # noqa: N818
    """Raised when an event handler is not found.

    Attributes:
        event_type: The type of event that caused the error.
    """

    event_type: type[Event]

    def __init__(self, msg: str, event_type: type[Event]) -> None:
        super().__init__(msg)
        self.event_type = event_type
