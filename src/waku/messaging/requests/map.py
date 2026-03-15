from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Self, TypeAlias

from typing_extensions import TypeVar

from waku.messaging._introspection import get_request_response_type
from waku.messaging.contracts.request import IRequest, RequestT
from waku.messaging.exceptions import MapFrozenError, RequestHandlerAlreadyRegistered
from waku.messaging.requests.handler import RequestHandler

if TYPE_CHECKING:
    from waku.messaging.contracts.message import ResponseT

__all__ = [
    'RequestMap',
    'RequestMapEntry',
    'RequestMapRegistry',
]

_MapReqT = TypeVar('_MapReqT', bound='IRequest[Any]', default='IRequest[Any]')
_MapResT = TypeVar('_MapResT', default=None)


@dataclass(frozen=True, slots=True)
class RequestMapEntry(Generic[_MapReqT, _MapResT]):
    handler_type: type[RequestHandler[_MapReqT, _MapResT]]
    di_lookup_type: type[RequestHandler[_MapReqT, _MapResT]]


RequestMapRegistry: TypeAlias = MutableMapping[type[IRequest[Any]], RequestMapEntry[IRequest[Any], Any]]


class RequestMap:
    def __init__(self) -> None:
        self._registry: RequestMapRegistry = {}
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def bind(
        self,
        request_type: type[RequestT],
        handler_type: type[RequestHandler[RequestT, ResponseT]],
    ) -> Self:
        if self._frozen:
            raise MapFrozenError
        if request_type in self._registry:
            raise RequestHandlerAlreadyRegistered(request_type, handler_type)
        response_type = get_request_response_type(request_type)
        di_lookup_type = RequestHandler[request_type, response_type]  # type: ignore[valid-type]
        self._registry[request_type] = RequestMapEntry(handler_type, di_lookup_type)  # type: ignore[type-abstract, arg-type]
        return self

    def merge(self, other: RequestMap) -> Self:
        if self._frozen:
            raise MapFrozenError
        for request_type, entry in other._registry.items():
            self.bind(request_type, entry.handler_type)
        return self

    @property
    def registry(self) -> RequestMapRegistry:
        return self._registry

    def has_handler(self, request_type: type[RequestT]) -> bool:
        return request_type in self._registry

    def get_handler_type(self, request_type: type[RequestT]) -> type[RequestHandler[RequestT, Any]]:
        return self._registry[request_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
