from __future__ import annotations

from typing import Any, Self, TypeAlias

from waku.ext.mediator.exceptions import RequestHandlerAlreadyRegistered, RequestHandlerNotFound
from waku.ext.mediator.handlers.handler import HandlerType, RequestT, ResponseT

__all__ = [
    'RequestMap',
    'RequestMapRegistry',
]

RequestMapRegistry: TypeAlias = dict[type[RequestT], HandlerType[RequestT, ResponseT]]


class RequestMap:
    def __init__(self) -> None:
        self._registry: RequestMapRegistry[Any, Any] = {}

    def bind(self, request_type: type[RequestT], handler_type: HandlerType[RequestT, ResponseT]) -> Self:
        if request_type in self._registry:
            msg = f'{request_type.__name__} already exists in registry'
            raise RequestHandlerAlreadyRegistered(msg, request_type, handler_type)
        self._registry[request_type] = handler_type
        return self

    def merge(self, other: RequestMap) -> Self:
        for request_type, handler_type in other.registry.items():
            self.bind(request_type, handler_type)
        return self

    @property
    def registry(self) -> RequestMapRegistry[Any, Any]:
        return self._registry

    def __getitem__(self, request_type: type[RequestT]) -> HandlerType[RequestT, ResponseT]:
        try:
            return self._registry[request_type]
        except KeyError as err:
            msg = f'Request handler for {request_type.__name__} request is not registered'
            raise RequestHandlerNotFound(msg, request_type) from err
