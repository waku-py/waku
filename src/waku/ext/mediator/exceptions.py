from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.ext.mediator.contracts.event import Event
    from waku.ext.mediator.contracts.request import Request
    from waku.ext.mediator.events.handler import EventHandlerType
    from waku.ext.mediator.requests.handler import RequestHandlerType

__all__ = [
    'EventHandlerAlreadyRegistered',
    'EventHandlerNotFound',
    'ImproperlyConfiguredError',
    'MediatorError',
    'RequestHandlerAlreadyRegistered',
    'RequestHandlerNotFound',
]


class MediatorError(Exception):
    pass


class ImproperlyConfiguredError(MediatorError):
    pass


class RequestHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
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
    request_type: type[Request[Any]]

    def __init__(self, msg: str, request_type: type[Request[Any]]) -> None:
        super().__init__(msg)
        self.request_type = request_type


class EventHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
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
    event_type: type[Event]

    def __init__(self, msg: str, event_type: type[Event]) -> None:
        super().__init__(msg)
        self.event_type = event_type
