from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.messaging.contracts.event import EventT, IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.endpoints.base import EndpointEntry, local_queue
from waku.messaging.events.handler import EventHandler
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.modules import MessagingConfig
from waku.messaging.registry import MessageRegistry
from waku.messaging.requests.handler import RequestHandler
from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor, RoutingTable
from waku.messaging.routing_builder import ModuleRoutingMap, RoutingTableBuilder

if TYPE_CHECKING:
    from collections.abc import Sequence


class _TestEvent(IEvent): ...


class _TestHandler(EventHandler[_TestEvent]):
    @override
    async def handle(self, event: _TestEvent, /) -> None: ...  # pragma: no cover


class _DummyModule: ...


def _make_registry_with_event(
    event_type: type[EventT],
    handler_types: Sequence[type[EventHandler[EventT]]],
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


def test_empty_config_produces_empty_routing_table() -> None:
    registry = MessageRegistry()
    config = _make_config()
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table == RoutingTable()


def test_route_descriptor_populates_type_and_handler_routes() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(RouteDescriptor(_TestEvent, 'queue://test'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table.type_routes[_TestEvent] == ('queue://test',)
    assert table.handler_routes[_TestEvent] == frozenset({_TestHandler})


def test_route_descriptor_with_unknown_uri_raises_error() -> None:
    registry = MessageRegistry()
    config = _make_config(
        routing=(RouteDescriptor(_TestEvent, 'queue://unknown'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='unknown'):
        RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()


def test_module_route_descriptor_populates_type_and_handler_routes() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(ModuleRouteDescriptor(_DummyModule, 'queue://test'),),
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()

    assert table.type_routes[_TestEvent] == ('queue://test',)
    assert table.handler_routes[_TestEvent] == frozenset({_TestHandler})


def test_module_route_descriptor_with_unknown_uri_raises_error() -> None:
    config = _make_config(
        routing=(ModuleRouteDescriptor(_DummyModule, 'queue://unknown'),),
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    with pytest.raises(ImproperlyConfiguredError, match='unknown'):
        RoutingTableBuilder(config, aggregated=MessageRegistry(), module_routing_map=module_routing_map).build()


def test_module_route_descriptor_with_unknown_module_raises_error() -> None:
    class _UnknownModule: ...

    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(ModuleRouteDescriptor(_UnknownModule, 'queue://test'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='_UnknownModule'):
        RoutingTableBuilder(config, aggregated=MessageRegistry(), module_routing_map={}).build()


def test_event_without_handlers_populates_type_routes_only() -> None:
    registry = MessageRegistry()
    registry.freeze()
    config = _make_config(
        endpoints=(local_queue('queue://events'),),
        routing=(RouteDescriptor(_TestEvent, 'queue://events'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert _TestEvent in table.type_routes
    assert _TestEvent not in table.handler_routes


def test_request_route_does_not_populate_handler_routes() -> None:
    registry = MessageRegistry()

    class _Cmd(IRequest[None]): ...

    class _CmdHandler(RequestHandler[_Cmd, None]):
        @override
        async def handle(self, request: _Cmd, /) -> None: ...  # pragma: no cover

    registry.request_map.bind(_Cmd, _CmdHandler)
    registry.freeze()
    config = _make_config(
        endpoints=(local_queue('queue://cmds'),),
        routing=(RouteDescriptor(_Cmd, 'queue://cmds'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert _Cmd in table.type_routes
    assert _Cmd not in table.handler_routes


@pytest.mark.parametrize(
    'routing',
    [
        pytest.param(
            (ModuleRouteDescriptor(_DummyModule, 'notifications'), RouteDescriptor(_TestEvent, 'priority')),
            id='module_first',
        ),
        pytest.param(
            (RouteDescriptor(_TestEvent, 'priority'), ModuleRouteDescriptor(_DummyModule, 'notifications')),
            id='per_type_first',
        ),
    ],
)
def test_per_type_route_takes_precedence_over_module_route(
    routing: tuple[RouteDescriptor | ModuleRouteDescriptor, ...],
) -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    config = _make_config(
        endpoints=(local_queue('notifications'), local_queue('priority')),
        routing=routing,
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()
    assert table.type_routes[_TestEvent] == ('priority',)


def test_two_module_routes_for_same_event_are_additive() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])

    class _ModA: ...

    class _ModB: ...

    config = _make_config(
        endpoints=(local_queue('queue-a'), local_queue('queue-b')),
        routing=(
            ModuleRouteDescriptor(_ModA, 'queue-a'),
            ModuleRouteDescriptor(_ModB, 'queue-b'),
        ),
    )
    module_routing_map: ModuleRoutingMap = {
        _ModA: {_TestEvent: [_TestHandler]},
        _ModB: {_TestEvent: [_TestHandler]},
    }
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()
    assert set(table.type_routes[_TestEvent]) == {'queue-a', 'queue-b'}
    assert table.handler_routes[_TestEvent] == frozenset({_TestHandler})


def test_per_type_override_only_affects_overridden_event() -> None:
    class _OtherEvent(IEvent): ...

    class _OtherHandler(EventHandler[_OtherEvent]):
        @override
        async def handle(self, event: _OtherEvent, /) -> None: ...  # pragma: no cover

    registry = MessageRegistry()
    registry.event_map.bind(_TestEvent, [_TestHandler])
    registry.event_map.bind(_OtherEvent, [_OtherHandler])
    registry.freeze()

    config = _make_config(
        endpoints=(local_queue('notifications'), local_queue('priority')),
        routing=(
            RouteDescriptor(_TestEvent, 'priority'),
            ModuleRouteDescriptor(_DummyModule, 'notifications'),
        ),
    )
    module_routing_map: ModuleRoutingMap = {
        _DummyModule: {_TestEvent: [_TestHandler], _OtherEvent: [_OtherHandler]},
    }
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()

    assert table.type_routes[_TestEvent] == ('priority',)
    assert table.type_routes[_OtherEvent] == ('notifications',)


def test_duplicate_route_descriptors_are_deduplicated() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(
            RouteDescriptor(_TestEvent, 'queue://test'),
            RouteDescriptor(_TestEvent, 'queue://test'),
        ),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table.type_routes[_TestEvent] == ('queue://test',)


def test_endpoint_entries_enriched_with_handler_subscriptions() -> None:
    registry = _make_registry_with_event(_TestEvent, [_TestHandler])
    config = _make_config(
        endpoints=(local_queue('routed-q'), local_queue('unused-q')),
        routing=(RouteDescriptor(_TestEvent, 'routed-q'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()

    routed_entry = next(e for e in table.entries if e.uri == 'routed-q')
    unused_entry = next(e for e in table.entries if e.uri == 'unused-q')

    assert routed_entry.handler_subscriptions[_TestEvent] == frozenset({_TestHandler})
    assert unused_entry.handler_subscriptions == {}
