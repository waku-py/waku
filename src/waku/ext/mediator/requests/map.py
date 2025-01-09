from __future__ import annotations

from typing import Any, Self, TypeAlias

from waku.ext.mediator.contracts.request import RequestT, ResponseT
from waku.ext.mediator.exceptions import RequestHandlerAlreadyRegistered
from waku.ext.mediator.requests.handler import RequestHandlerType

__all__ = [
    'RequestMap',
    'RequestMapRegistry',
]

RequestMapRegistry: TypeAlias = dict[type[RequestT], RequestHandlerType[RequestT, ResponseT]]


class RequestMap:
    def __init__(self) -> None:
        self._registry: RequestMapRegistry[Any, Any] = {}

    def bind(self, request_type: type[RequestT], handler_type: RequestHandlerType[RequestT, ResponseT]) -> Self:
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

    def __bool__(self) -> bool:
        return bool(self._registry)
