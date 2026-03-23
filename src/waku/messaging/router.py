from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.messaging.contracts.message import IMessage
    from waku.messaging.endpoints.base import Endpoint, EndpointEntry
    from waku.messaging.events.handler import EventHandler

_EMPTY_TYPE_ROUTES: Mapping[type[IMessage], tuple[str, ...]] = MappingProxyType({})
_EMPTY_HANDLER_ROUTES: Mapping[type[IMessage], frozenset[type[EventHandler[Any]]]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RoutingTable:
    entries: tuple[EndpointEntry, ...] = ()
    type_routes: Mapping[type[IMessage], tuple[str, ...]] = field(default_factory=lambda: _EMPTY_TYPE_ROUTES)
    handler_routes: Mapping[type[IMessage], frozenset[type[EventHandler[Any]]]] = field(
        default_factory=lambda: _EMPTY_HANDLER_ROUTES
    )


class MessageRouter:
    __slots__ = ('_endpoints', '_handler_routes', '_routes')

    def __init__(
        self,
        routes: dict[type[IMessage], list[Endpoint]],
        handler_routes: dict[type[IMessage], frozenset[type[EventHandler[Any]]]],
        endpoints: Sequence[Endpoint] = (),
    ) -> None:
        self._routes: dict[type[IMessage], tuple[Endpoint, ...]] = {k: tuple(v) for k, v in routes.items()}
        self._handler_routes = handler_routes
        self._endpoints = tuple(endpoints)

    @property
    def endpoints(self) -> tuple[Endpoint, ...]:
        return self._endpoints

    def resolve(self, message_type: type[IMessage]) -> tuple[Endpoint, ...]:
        return self._routes.get(message_type, ())

    def routed_handler_types(self, message_type: type[IMessage]) -> frozenset[type[EventHandler[Any]]]:
        return self._handler_routes.get(message_type, frozenset())


@dataclass(frozen=True, slots=True)
class RouteDescriptor:
    message_type: type[IMessage]
    endpoint_uri: str


@dataclass(frozen=True, slots=True)
class ModuleRouteDescriptor:
    module_type: type
    endpoint_uri: str


class RouteBuilder:
    __slots__ = ('_message_type',)

    def __init__(self, message_type: type[IMessage]) -> None:
        self._message_type = message_type

    def to(self, endpoint_uri: str) -> RouteDescriptor:
        return RouteDescriptor(self._message_type, endpoint_uri)


class ModuleRouteBuilder:
    __slots__ = ('_module_type',)

    def __init__(self, module_type: type) -> None:
        self._module_type = module_type

    def events_to(self, endpoint_uri: str) -> ModuleRouteDescriptor:
        return ModuleRouteDescriptor(self._module_type, endpoint_uri)


def route(message_type: type[IMessage]) -> RouteBuilder:
    return RouteBuilder(message_type)


def route_module(module_type: type) -> ModuleRouteBuilder:
    return ModuleRouteBuilder(module_type)
