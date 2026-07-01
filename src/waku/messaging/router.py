from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.endpoints.base import Endpoint, LocalQueueEntry
    from waku.messaging.endpoints.merge import MergedBrokerEndpoint
    from waku.modules import ModuleType

HandlerSubscriptions: TypeAlias = 'Mapping[type[IMessage], frozenset[HandlerType]]'


@dataclass(frozen=True, slots=True)
class RoutingTable:
    entries: tuple[MergedBrokerEndpoint | LocalQueueEntry, ...] = ()
    type_routes: Mapping[type[IMessage], tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    endpoint_subscriptions: Mapping[str, HandlerSubscriptions] = field(default_factory=lambda: MappingProxyType({}))


class MessageRouter:
    __slots__ = ('_by_uri', '_endpoints', '_routes')

    def __init__(
        self,
        routes: Mapping[type[IMessage], Sequence[Endpoint]],
        endpoints: Sequence[Endpoint],
    ) -> None:
        self._routes = routes
        self._endpoints = endpoints
        self._by_uri = {endpoint.uri: endpoint for endpoint in endpoints}

    @property
    def endpoints(self) -> Sequence[Endpoint]:
        return self._endpoints

    def resolve(self, message_type: type[IMessage]) -> Sequence[Endpoint]:
        return self._routes.get(message_type, ())

    def endpoint_for(self, uri: str) -> Endpoint | None:
        return self._by_uri.get(uri)


@dataclass(frozen=True, slots=True)
class RouteDescriptor:
    message_type: type[IMessage]
    endpoint_uri: str


@dataclass(frozen=True, slots=True)
class ModuleRouteDescriptor:
    module_type: ModuleType
    endpoint_uri: str


class RouteBuilder:
    __slots__ = ('_message_type',)

    def __init__(self, message_type: type[IMessage]) -> None:
        self._message_type = message_type

    def to(self, endpoint_uri: str) -> RouteDescriptor:
        return RouteDescriptor(self._message_type, endpoint_uri)


class ModuleRouteBuilder:
    __slots__ = ('_module_type',)

    def __init__(self, module_type: ModuleType) -> None:
        self._module_type = module_type

    def to(self, endpoint_uri: str) -> ModuleRouteDescriptor:
        return ModuleRouteDescriptor(self._module_type, endpoint_uri)


def route(message_type: type[IMessage]) -> RouteBuilder:
    return RouteBuilder(message_type)


def route_module(module_type: ModuleType) -> ModuleRouteBuilder:
    return ModuleRouteBuilder(module_type)
