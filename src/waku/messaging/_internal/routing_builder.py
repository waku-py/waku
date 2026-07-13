from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint
from waku.messaging.endpoints.base import DEFAULT_ENDPOINT_URI, LocalQueueEntry
from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor, RoutingTable, local_queue

if TYPE_CHECKING:
    from waku.messages import IMessage
    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.handler_map import HandlerMap
    from waku.modules import ModuleType

__all__ = [
    'ModuleRoutingMap',
    'RoutingTableBuilder',
]

ModuleRoutingMap = Mapping['ModuleType', Mapping[type['IMessage'], Sequence['HandlerType']]]


class RoutingTableBuilder:
    __slots__ = (
        '_config',
        '_endpoint_handlers',
        '_merged_endpoints',
        '_module_routing_map',
        '_per_type_overrides',
        '_registry',
        '_type_routes',
    )

    def __init__(
        self,
        config: MessagingConfig,
        *,
        merged_endpoints: tuple[MergedBrokerEndpoint, ...] = (),
        handler_map: HandlerMap,
        module_routing_map: ModuleRoutingMap,
    ) -> None:
        self._config = config
        self._merged_endpoints = merged_endpoints
        self._registry = handler_map
        self._module_routing_map = module_routing_map
        self._type_routes: defaultdict[type[IMessage], list[str]] = defaultdict(list)
        self._endpoint_handlers: defaultdict[str, defaultdict[type[IMessage], set[HandlerType]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._per_type_overrides: set[type[IMessage]] = set()

    def build(self) -> RoutingTable:
        endpoints = self._collect_endpoint_entries()
        self._apply_routes(endpoints)
        self._assign_unrouted_to_default()
        self._ensure_default_endpoint(endpoints)
        return self._assemble(endpoints)

    def _collect_endpoint_entries(self) -> dict[str, MergedBrokerEndpoint | LocalQueueEntry]:
        entries: dict[str, MergedBrokerEndpoint | LocalQueueEntry] = {ep.uri: ep for ep in self._merged_endpoints}
        for entry in self._config.endpoints:
            if isinstance(entry, LocalQueueEntry):
                entries[entry.uri] = entry
        return entries

    def _apply_routes(self, endpoints: Mapping[str, MergedBrokerEndpoint | LocalQueueEntry]) -> None:
        per_type: list[RouteDescriptor] = []
        module_level: list[ModuleRouteDescriptor] = []

        for descriptor in self._config.routing:
            self._validate_endpoint_uri(descriptor.endpoint_uri, endpoints)
            match descriptor:
                case RouteDescriptor():
                    per_type.append(descriptor)
                case ModuleRouteDescriptor():  # pragma: no branch
                    module_level.append(descriptor)

        for descriptor in per_type:
            self._apply_per_type_route(descriptor)
        for descriptor in module_level:
            self._apply_module_route(descriptor)

    def _apply_per_type_route(self, descriptor: RouteDescriptor) -> None:
        msg_type = descriptor.message_type
        self._per_type_overrides.add(msg_type)
        self._type_routes[msg_type].append(descriptor.endpoint_uri)

        handlers = self._registry.get_handler_types(msg_type)
        if not handlers:
            msg = f"route() references '{msg_type.__qualname__}' which has no registered handlers"
            raise ImproperlyConfiguredError(msg)
        self._endpoint_handlers[descriptor.endpoint_uri][msg_type].update(handlers)

    def _apply_module_route(self, descriptor: ModuleRouteDescriptor) -> None:
        module_type = descriptor.module_type
        if module_type not in self._module_routing_map:
            msg = f"route_module() references module '{module_type.__qualname__}' which has no registered handlers"
            raise ImproperlyConfiguredError(msg)

        for msg_type, handler_types in self._module_routing_map[module_type].items():
            if msg_type in self._per_type_overrides:
                continue
            self._type_routes[msg_type].append(descriptor.endpoint_uri)
            self._endpoint_handlers[descriptor.endpoint_uri][msg_type].update(handler_types)

    def _assign_unrouted_to_default(self) -> None:
        for msg_type, handlers in self._registry.items():
            all_handlers: set[HandlerType] = set(handlers)
            routed = self._collect_routed_handlers(msg_type)
            unrouted = all_handlers - routed
            if unrouted:
                self._type_routes[msg_type].append(DEFAULT_ENDPOINT_URI)
                self._endpoint_handlers[DEFAULT_ENDPOINT_URI][msg_type].update(unrouted)

    def _collect_routed_handlers(self, msg_type: type[IMessage]) -> set[HandlerType]:
        routed: set[HandlerType] = set()
        for ep_handlers in self._endpoint_handlers.values():
            routed.update(ep_handlers.get(msg_type, set()))
        return routed

    def _ensure_default_endpoint(self, endpoints: dict[str, MergedBrokerEndpoint | LocalQueueEntry]) -> None:
        if DEFAULT_ENDPOINT_URI in endpoints:
            return
        needs_default = any(DEFAULT_ENDPOINT_URI in uris for uris in self._type_routes.values())
        if needs_default:
            endpoints[DEFAULT_ENDPOINT_URI] = local_queue(DEFAULT_ENDPOINT_URI)

    def _assemble(self, endpoints: Mapping[str, MergedBrokerEndpoint | LocalQueueEntry]) -> RoutingTable:
        type_routes = {msg_type: tuple(dict.fromkeys(uris)) for msg_type, uris in self._type_routes.items()}

        endpoint_subscriptions: dict[str, dict[type[IMessage], frozenset[HandlerType]]] = {}
        for uri, handlers_by_type in self._endpoint_handlers.items():
            subs = {msg_type: frozenset(handlers) for msg_type, handlers in handlers_by_type.items() if handlers}
            if subs:
                endpoint_subscriptions[uri] = subs

        return RoutingTable(
            entries=tuple(endpoints.values()),
            type_routes=MappingProxyType(type_routes),
            endpoint_subscriptions=MappingProxyType(endpoint_subscriptions),
        )

    @staticmethod
    def _validate_endpoint_uri(uri: str, known: Mapping[str, MergedBrokerEndpoint | LocalQueueEntry]) -> None:
        if uri not in known:
            msg = f"route references unknown endpoint URI '{uri}'. Known endpoints: {sorted(known)}"
            raise ImproperlyConfiguredError(msg)
        entry = known[uri]
        if isinstance(entry, MergedBrokerEndpoint) and entry.send is None:
            msg = (
                f"Route targets '{uri}', a listen-only endpoint (no send aspect); cannot dispatch to it — "
                'declare external_endpoint(...) for this URI or route elsewhere'
            )
            raise ImproperlyConfiguredError(msg)
