from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Generic, Self, TypeAlias

from typing_extensions import TypeVar

from waku.cqrs.contracts.request import IRequest, RequestT, Response, ResponseT
from waku.cqrs.exceptions import RequestHandlerAlreadyRegistered
from waku.cqrs.requests.handler import RequestHandler
from waku.cqrs.utils import get_request_response_type

__all__ = [
    'RequestMap',
    'RequestMapEntry',
    'RequestMapRegistry',
]

_MapReqT = TypeVar('_MapReqT', bound='IRequest[Response | None]', default='IRequest[Response | None]')
_MapResT = TypeVar('_MapResT', bound='Response | None', default='Response | None')


@dataclass(frozen=True, slots=True)
class RequestMapEntry(Generic[_MapReqT, _MapResT]):
    handler_type: type[RequestHandler[_MapReqT, _MapResT]]
    di_lookup_type: type[RequestHandler[_MapReqT, _MapResT]]


RequestMapRegistry: TypeAlias = MutableMapping[
    type[IRequest[Response | None]], RequestMapEntry[IRequest[Response | None], Response | None]
]


class RequestMap:
    def __init__(self) -> None:
        self._registry: RequestMapRegistry = {}

    def bind(
        self,
        request_type: type[RequestT],
        handler_type: type[RequestHandler[RequestT, ResponseT]],
    ) -> Self:
        if request_type in self._registry:
            raise RequestHandlerAlreadyRegistered(request_type, handler_type)
        response_type = get_request_response_type(request_type)
        di_lookup_type = RequestHandler[request_type, response_type]  # type: ignore[valid-type]
        self._registry[request_type] = RequestMapEntry(handler_type, di_lookup_type)  # type: ignore[type-abstract, arg-type]
        return self

    def merge(self, other: RequestMap) -> Self:
        for request_type, entry in other._registry.items():
            if request_type in self._registry:
                raise RequestHandlerAlreadyRegistered(request_type, entry.handler_type)
            self._registry[request_type] = RequestMapEntry(entry.handler_type, entry.di_lookup_type)
        return self

    @property
    def registry(self) -> RequestMapRegistry:
        return self._registry

    def has_handler(self, request_type: type[RequestT]) -> bool:
        return request_type in self._registry

    def get_handler_type(self, request_type: type[RequestT]) -> type[RequestHandler[RequestT, Response | None]]:
        return self._registry[request_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
