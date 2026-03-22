from __future__ import annotations

import pytest
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.endpoints.base import EndpointEntry, local_queue
from waku.messaging.events.handler import EventHandler
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.modules import MessagingConfig
from waku.messaging.registry import MessageRegistry
from waku.messaging.requests.handler import RequestHandler
from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor, RoutingTable
from waku.messaging.routing_builder import RoutingTableBuilder


class _TestEvent(IEvent): ...


class _TestHandler(EventHandler[_TestEvent]):
    @override
    async def handle(self, event: _TestEvent, /) -> None: ...  # pragma: no cover


def _make_registry_with_event(
    event_type: type[IEvent],
    handler_types: list[type],
) -> MessageRegistry:
    reg = MessageRegistry()
    reg.event_map.bind(event_type, handler_types)
    reg.freeze()
    return reg


def _make_config(
    endpoints: tuple[EndpointEntry, ...] = (),
    routing: tuple[RouteDescriptor | ModuleRouteDescriptor, ...] = (),
) -> MessagingConfig:
    return MessagingConfig(endpoints=endpoints, routing=routing)


def test_build_empty_routing_table() -> None:
    registry = MessageRegistry()
    config = _make_config()
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert table == RoutingTable()


def test_build_with_route_descriptor() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    endpoint = local_queue('queue://test')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(RouteDescriptor(_TestEvent, 'queue://test'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert _TestEvent in table.type_routes
    assert 'queue://test' in table.type_routes[_TestEvent]


def test_build_validates_unknown_endpoint_uri() -> None:
    registry = MessageRegistry()
    config = _make_config(
        routing=(RouteDescriptor(_TestEvent, 'queue://unknown'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='unknown'):
        RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()


def test_build_collects_handler_routes_for_events() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    endpoint = local_queue('queue://test')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(RouteDescriptor(_TestEvent, 'queue://test'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert _TestHandler in table.handler_routes[_TestEvent]


def test_build_with_module_route_descriptor() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])

    class _DummyModule: ...  # pragma: no cover

    endpoint = local_queue('queue://test')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(ModuleRouteDescriptor(_DummyModule, 'queue://test'),),
    )
    module_event_types: dict[type, list[type[IEvent]]] = {_DummyModule: [_TestEvent]}
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types=module_event_types).build()

    assert _TestEvent in table.type_routes
    assert _TestHandler in table.handler_routes[_TestEvent]


def test_build_validates_unknown_module_in_route_module() -> None:
    registry = MessageRegistry()

    class _UnknownModule: ...  # pragma: no cover

    endpoint = local_queue('queue://test')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(ModuleRouteDescriptor(_UnknownModule, 'queue://test'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='_UnknownModule'):
        RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()


def test_build_routes_event_without_handlers() -> None:
    registry = MessageRegistry()
    registry.freeze()
    endpoint = local_queue('queue://events')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(RouteDescriptor(_TestEvent, 'queue://events'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert _TestEvent in table.type_routes
    assert _TestEvent not in table.handler_routes


def test_build_skips_handler_routes_for_requests() -> None:
    registry = MessageRegistry()

    class _Cmd(IRequest[None]): ...

    class _CmdHandler(RequestHandler[_Cmd, None]):
        @override
        async def handle(self, request: _Cmd, /) -> None: ...  # pragma: no cover

    registry.request_map.bind(_Cmd, _CmdHandler)
    registry.freeze()
    endpoint = local_queue('queue://cmds')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(RouteDescriptor(_Cmd, 'queue://cmds'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert _Cmd in table.type_routes
    assert _Cmd not in table.handler_routes


def test_build_deduplicates_same_route() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    endpoint = local_queue('queue://test')
    config = _make_config(
        endpoints=(endpoint,),
        routing=(
            RouteDescriptor(_TestEvent, 'queue://test'),
            RouteDescriptor(_TestEvent, 'queue://test'),
        ),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_event_types={}).build()
    assert table.type_routes[_TestEvent] == ('queue://test',)
