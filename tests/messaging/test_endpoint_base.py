from __future__ import annotations

from types import MappingProxyType
from typing import Any

from waku.messaging.endpoints.base import (
    Endpoint,
    EndpointEntry,
    EndpointKind,
    external_endpoint,
    local_queue,
)


class _ConcreteEndpoint(Endpoint):
    async def dispatch(self, envelope: Any, scope: Any) -> None:  # pragma: no cover
        pass

    async def start(self) -> None:  # pragma: no cover
        pass

    async def stop(self) -> None:  # pragma: no cover
        pass


class TestEndpointProperties:
    @staticmethod
    def test_uri_property() -> None:
        ep = _ConcreteEndpoint(uri='test://ep', handler_subscriptions={})
        assert ep.uri == 'test://ep'

    @staticmethod
    def test_handler_subscriptions_property() -> None:
        subs: dict[Any, frozenset[type]] = {}
        ep = _ConcreteEndpoint(uri='test://ep', handler_subscriptions=subs)
        assert ep.handler_subscriptions is subs


class TestEndpointEntryFactories:
    @staticmethod
    def test_local_queue_creates_entry_with_defaults() -> None:
        entry = local_queue('q://test')
        assert entry == EndpointEntry(uri='q://test', kind=EndpointKind.LOCAL_QUEUE, stop_timeout=5.0)

    @staticmethod
    def test_local_queue_with_custom_timeout() -> None:
        entry = local_queue('q://test', stop_timeout=10.0)
        assert entry.stop_timeout == 10.0

    @staticmethod
    def test_external_endpoint_creates_entry() -> None:
        entry = external_endpoint('ext://bus')
        assert entry == EndpointEntry(
            uri='ext://bus',
            kind=EndpointKind.EXTERNAL,
            handler_subscriptions=MappingProxyType({}),
        )
