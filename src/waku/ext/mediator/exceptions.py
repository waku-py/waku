from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.ext.mediator.handlers.handler import HandlerType, Request


__all__ = [
    'MediatorError',
    'RequestHandlerAlreadyRegistered',
    'RequestHandlerNotFound',
]


class MediatorError(Exception):
    pass


class RequestHandlerAlreadyRegistered(MediatorError, KeyError):  # noqa: N818
    request_type: type[Request[Any]]
    handler_type: HandlerType[Any, Any]

    def __init__(
        self,
        msg: str,
        request_type: type[Request[Any]],
        handler_type: HandlerType[Any, Any],
    ) -> None:
        super().__init__(msg)
        self.request_type = request_type
        self.handler_type = handler_type


class RequestHandlerNotFound(MediatorError, TypeError):  # noqa: N818
    request_type: type[Request[Any]]

    def __init__(self, msg: str, request_type: type[Request[Any]]) -> None:
        super().__init__(msg)
        self.request_type = request_type
