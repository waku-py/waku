from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, assert_never

from waku.messaging.contracts.event import IEvent
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor, RoutingTable

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.events.handler import EventHandler
    from waku.messaging.modules import MessagingConfig
    from waku.messaging.registry import MessageRegistry

__all__ = ['ModuleRoutingMap', 'RoutingTableBuilder']

ModuleRoutingMap = Mapping[type, Mapping[type[IEvent], Sequence[type['EventHandler[Any]']]]]


class RoutingTableBuilder:
    __slots__ = (
        '_config',
        '_handler_routes',
        '_module_routing_map',
        '_per_type_overrides',
        '_registry',
        '_type_routes',
    )

    def __init__(
        self,
        config: MessagingConfig,
        *,
        aggregated: MessageRegistry,
        module_routing_map: ModuleRoutingMap,
    ) -> None:
        self._config = config
        self._registry = aggregated
        self._module_routing_map = module_routing_map
        self._type_routes: defaultdict[type[IMessage], list[str]] = defaultdict(list)
        self._handler_routes: defaultdict[type[IMessage], set[type[EventHandler[Any]]]] = defaultdict(set)
        self._per_type_overrides: set[type[IMessage]] = set()

    def build(self) -> RoutingTable:
        endpoint_entries_by_uri = {entry.uri: entry for entry in self._config.endpoints}

        route_descriptors: list[RouteDescriptor] = []
        module_descriptors: list[ModuleRouteDescriptor] = []

        for descriptor in self._config.routing:
            self._validate_endpoint_uri(descriptor.endpoint_uri, endpoint_entries_by_uri)
            match descriptor:
                case RouteDescriptor():
                    route_descriptors.append(descriptor)
                case ModuleRouteDescriptor():
                    module_descriptors.append(descriptor)
                case _:
                    assert_never(descriptor)

        for descriptor in route_descriptors:
            self._process_route(descriptor)
        for descriptor in module_descriptors:
            self._process_module_route(descriptor)

        return self._assemble(endpoint_entries_by_uri)

    def _process_route(self, descriptor: RouteDescriptor) -> None:
        msg_type = descriptor.message_type
        self._per_type_overrides.add(msg_type)
        self._type_routes[msg_type].append(descriptor.endpoint_uri)

        if issubclass(msg_type, IEvent):
            self._handler_routes[msg_type].update(self._registry.event_map.get_handler_types(msg_type))

    def _process_module_route(self, descriptor: ModuleRouteDescriptor) -> None:
        module_type = descriptor.module_type
        if module_type not in self._module_routing_map:
            msg = f"route_module() references module '{module_type.__qualname__}' which has no registered events"
            raise ImproperlyConfiguredError(msg)

        for event_type, handler_types in self._module_routing_map[module_type].items():
            if event_type in self._per_type_overrides:
                continue
            self._type_routes[event_type].append(descriptor.endpoint_uri)
            self._handler_routes[event_type].update(handler_types)

    def _assemble(self, endpoint_entries_by_uri: dict[str, EndpointEntry]) -> RoutingTable:
        frozen_type_routes = {msg_type: tuple(dict.fromkeys(uris)) for msg_type, uris in self._type_routes.items()}
        frozen_handler_routes = {
            msg_type: frozenset(handlers) for msg_type, handlers in self._handler_routes.items() if handlers
        }

        enriched_entries: list[EndpointEntry] = []
        for uri, entry in endpoint_entries_by_uri.items():
            subs: dict[type[IMessage], frozenset[type[EventHandler[Any]]]] = {}
            for msg_type, uris in self._type_routes.items():
                if uri in uris and msg_type in frozen_handler_routes:
                    subs[msg_type] = frozen_handler_routes[msg_type]

            enriched_entries.append(replace(entry, handler_subscriptions=subs))

        return RoutingTable(
            entries=tuple(enriched_entries),
            type_routes=frozen_type_routes,
            handler_routes=frozen_handler_routes,
        )

    @staticmethod
    def _validate_endpoint_uri(uri: str, known: dict[str, EndpointEntry]) -> None:
        if uri not in known:
            msg = f"Route references unknown endpoint URI '{uri}'. Known endpoints: {sorted(known)}"
            raise ImproperlyConfiguredError(msg)
