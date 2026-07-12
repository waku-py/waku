from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging.config import MessagingConfig
from waku.messaging.contracts.request import IRequest
from waku.messaging.endpoints.base import LocalQueueEntry

if TYPE_CHECKING:
    from waku.messages import IMessage
    from waku.messaging.contracts.handler import HandlerType

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import HandlerMap
from waku.messaging._internal.routing_builder import ModuleRoutingMap, RoutingTableBuilder
from waku.messaging.endpoints.base import DEFAULT_ENDPOINT_URI, EndpointEntry
from waku.messaging.handler import EventHandler, RequestHandler
from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor, local_queue


class _TestEvent(IEvent): ...


class _TestHandler(EventHandler[_TestEvent]):
    @override
    async def handle(self, event: _TestEvent, /) -> None: ...  # pragma: no cover


class _DummyModule: ...


def _make_registry_with_handlers(
    message_type: type[IMessage],
    *handler_types: HandlerType,
) -> HandlerMap:
    reg = HandlerMap()
    for handler_type in handler_types:
        reg.bind(message_type, handler_type)
    reg.freeze()
    return reg


def _make_config(
    endpoints: tuple[EndpointEntry, ...] = (),
    routing: tuple[RouteDescriptor | ModuleRouteDescriptor, ...] = (),
) -> MessagingConfig:
    return MessagingConfig(endpoints=endpoints, routing=routing)


def test_empty_config_with_no_handlers_produces_empty_table() -> None:
    registry = HandlerMap()
    config = _make_config()
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table.entries == ()
    assert table.type_routes == {}
    assert table.endpoint_subscriptions == {}


def test_config_with_handlers_auto_creates_default_endpoint() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config()
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert any(e.uri == DEFAULT_ENDPOINT_URI for e in table.entries)


def test_route_descriptor_populates_type_routes_and_subscriptions() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(RouteDescriptor(_TestEvent, 'queue://test'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table.type_routes[_TestEvent] == ('queue://test',)
    assert table.endpoint_subscriptions['queue://test'][_TestEvent] == frozenset({_TestHandler})


def test_route_descriptor_with_unknown_uri_raises_error() -> None:
    registry = HandlerMap()
    config = _make_config(
        routing=(RouteDescriptor(_TestEvent, 'queue://unknown'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='unknown'):
        RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()


def test_module_route_descriptor_populates_type_routes_and_subscriptions() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(ModuleRouteDescriptor(_DummyModule, 'queue://test'),),
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()

    assert table.type_routes[_TestEvent] == ('queue://test',)
    assert table.endpoint_subscriptions['queue://test'][_TestEvent] == frozenset({_TestHandler})


def test_module_route_descriptor_with_unknown_uri_raises_error() -> None:
    config = _make_config(
        routing=(ModuleRouteDescriptor(_DummyModule, 'queue://unknown'),),
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    with pytest.raises(ImproperlyConfiguredError, match='unknown'):
        RoutingTableBuilder(config, aggregated=HandlerMap(), module_routing_map=module_routing_map).build()


def test_module_route_descriptor_with_unknown_module_raises_error() -> None:
    class _UnknownModule: ...

    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(ModuleRouteDescriptor(_UnknownModule, 'queue://test'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='_UnknownModule'):
        RoutingTableBuilder(config, aggregated=HandlerMap(), module_routing_map={}).build()


def test_route_without_handlers_raises_error() -> None:
    registry = HandlerMap()
    registry.freeze()
    config = _make_config(
        endpoints=(local_queue('queue://events'),),
        routing=(RouteDescriptor(_TestEvent, 'queue://events'),),
    )
    with pytest.raises(ImproperlyConfiguredError, match='_TestEvent'):
        RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()


def test_request_route_populates_type_routes_and_subscriptions() -> None:
    registry = HandlerMap()

    class _Cmd(IRequest[None]): ...

    class _CmdHandler(RequestHandler[_Cmd, None]):
        @override
        async def handle(self, request: _Cmd, /) -> None: ...  # pragma: no cover

    registry.bind(_Cmd, _CmdHandler)
    registry.freeze()
    config = _make_config(
        endpoints=(local_queue('queue://cmds'),),
        routing=(RouteDescriptor(_Cmd, 'queue://cmds'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert _Cmd in table.type_routes
    assert table.endpoint_subscriptions['queue://cmds'][_Cmd] == frozenset({_CmdHandler})


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
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config(
        endpoints=(local_queue('notifications'), local_queue('priority')),
        routing=routing,
    )
    module_routing_map: ModuleRoutingMap = {_DummyModule: {_TestEvent: [_TestHandler]}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()
    assert table.type_routes[_TestEvent] == ('priority',)


def test_two_module_routes_for_same_event_are_additive() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)

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


def test_per_type_override_only_affects_overridden_event() -> None:
    class _OtherEvent(IEvent): ...

    class _OtherHandler(EventHandler[_OtherEvent]):
        @override
        async def handle(self, event: _OtherEvent, /) -> None: ...  # pragma: no cover

    registry = HandlerMap()
    registry.bind(_TestEvent, _TestHandler)
    registry.bind(_OtherEvent, _OtherHandler)
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
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config(
        endpoints=(local_queue('queue://test'),),
        routing=(
            RouteDescriptor(_TestEvent, 'queue://test'),
            RouteDescriptor(_TestEvent, 'queue://test'),
        ),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()
    assert table.type_routes[_TestEvent] == ('queue://test',)


def test_subscriptions_populated_for_routed_endpoint() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config(
        endpoints=(local_queue('routed-q'), local_queue('unused-q')),
        routing=(RouteDescriptor(_TestEvent, 'routed-q'),),
    )
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()

    assert table.endpoint_subscriptions['routed-q'][_TestEvent] == frozenset({_TestHandler})
    assert 'unused-q' not in table.endpoint_subscriptions


def test_explicit_default_endpoint_is_not_auto_created() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    custom_default = local_queue(DEFAULT_ENDPOINT_URI, stop_timeout=99.0)
    config = _make_config(endpoints=(custom_default,))
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()

    default_entries = [e for e in table.entries if e.uri == DEFAULT_ENDPOINT_URI]
    assert len(default_entries) == 1
    entry = default_entries[0]
    assert isinstance(entry, LocalQueueEntry)
    assert entry.stop_timeout == 99.0


def test_unrouted_events_assigned_to_default_endpoint() -> None:
    registry = _make_registry_with_handlers(_TestEvent, _TestHandler)
    config = _make_config()
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map={}).build()

    assert table.endpoint_subscriptions[DEFAULT_ENDPOINT_URI][_TestEvent] == frozenset({_TestHandler})
    assert _TestEvent in table.type_routes


def test_partially_routed_event_splits_handlers() -> None:
    class _HandlerA(EventHandler[_TestEvent]):
        @override
        async def handle(self, event: _TestEvent, /) -> None: ...  # pragma: no cover

    class _HandlerB(EventHandler[_TestEvent]):
        @override
        async def handle(self, event: _TestEvent, /) -> None: ...  # pragma: no cover

    registry = HandlerMap()
    registry.bind(_TestEvent, _HandlerA)
    registry.bind(_TestEvent, _HandlerB)
    registry.freeze()

    class _ModA: ...

    config = _make_config(
        endpoints=(local_queue('queue-a'),),
        routing=(ModuleRouteDescriptor(_ModA, 'queue-a'),),
    )
    module_routing_map: ModuleRoutingMap = {_ModA: {_TestEvent: [_HandlerA]}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()

    assert table.endpoint_subscriptions['queue-a'][_TestEvent] == frozenset({_HandlerA})
    assert table.endpoint_subscriptions[DEFAULT_ENDPOINT_URI][_TestEvent] == frozenset({_HandlerB})


def test_endpoint_with_empty_handler_list_excluded_from_subscriptions() -> None:
    registry = HandlerMap()
    registry.freeze()

    class _Mod: ...

    config = _make_config(
        endpoints=(local_queue('queue://routed'),),
        routing=(ModuleRouteDescriptor(_Mod, 'queue://routed'),),
    )
    module_routing_map: ModuleRoutingMap = {_Mod: {_TestEvent: []}}
    table = RoutingTableBuilder(config, aggregated=registry, module_routing_map=module_routing_map).build()

    assert any(e.uri == 'queue://routed' for e in table.entries)
    assert 'queue://routed' not in table.endpoint_subscriptions
