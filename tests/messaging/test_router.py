from __future__ import annotations

from typing import Any

from typing_extensions import override

from waku.messaging.contracts.message import IMessage
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.router import (
    MessageRouter,
    ModuleRouteDescriptor,
    RouteDescriptor,
    route,
    route_module,
)


class _StubEndpoint(Endpoint):
    def __init__(self, uri: str = 'stub://default') -> None:
        super().__init__(uri=uri)

    @override
    async def dispatch(self, envelope: Any, scope: Any) -> None:  # pragma: no cover
        pass

    @override
    async def start(self) -> None:  # pragma: no cover
        pass

    @override
    async def stop(self) -> None:  # pragma: no cover
        pass


class _MessageA(IMessage):
    pass


class _MessageB(IMessage):
    pass


class _SomeModule:
    pass


class TestMessageRouter:
    @staticmethod
    def test_resolve_returns_endpoints_for_routed_message() -> None:
        endpoint = _StubEndpoint()
        router = MessageRouter(routes={_MessageA: (endpoint,)}, endpoints=(endpoint,))

        result = router.resolve(_MessageA)

        assert list(result) == [endpoint]

    @staticmethod
    def test_resolve_returns_empty_sequence_for_unrouted_message() -> None:
        router = MessageRouter(routes={}, endpoints=())

        result = router.resolve(_MessageA)

        assert list(result) == []

    @staticmethod
    def test_resolve_returns_multiple_endpoints_for_same_message() -> None:
        endpoint_a = _StubEndpoint(uri='stub://a')
        endpoint_b = _StubEndpoint(uri='stub://b')
        router = MessageRouter(routes={_MessageA: (endpoint_a, endpoint_b)}, endpoints=(endpoint_a, endpoint_b))

        result = router.resolve(_MessageA)

        assert list(result) == [endpoint_a, endpoint_b]


class TestRouteHelpers:
    @staticmethod
    def test_route_to_creates_route_descriptor() -> None:
        descriptor = route(_MessageA).to('queue://orders')

        assert descriptor == RouteDescriptor(message_type=_MessageA, endpoint_uri='queue://orders')

    @staticmethod
    def test_route_module_to_creates_module_route_descriptor() -> None:
        descriptor = route_module(_SomeModule).to('queue://events')

        assert descriptor == ModuleRouteDescriptor(module_type=_SomeModule, endpoint_uri='queue://events')
