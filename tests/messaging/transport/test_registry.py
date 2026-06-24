from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.registry import TransportRegistry, resolve_default_scheme, split_destination

from tests.messaging.helpers import StubSubscription

if TYPE_CHECKING:
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import Subscription, WireMetadata


class StubTransport(ITransport):
    """Minimal ITransport double — no-op on all methods."""

    @override
    async def send(self, body: dict[str, Any], *, destination: str, metadata: WireMetadata) -> None:
        pass  # pragma: no cover

    @override
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> Subscription:
        return StubSubscription()  # pragma: no cover

    @override
    async def start(self) -> None:
        pass  # pragma: no cover

    @override
    async def stop(self) -> None:
        pass  # pragma: no cover


class TestSplitDestination:
    @staticmethod
    def test_explicit_scheme_parsed() -> None:
        scheme, queue = split_destination('rabbitmq://orders', default_scheme=None)
        assert scheme == 'rabbitmq'
        assert queue == 'orders'

    @staticmethod
    def test_bare_uri_uses_explicit_default() -> None:
        scheme, queue = split_destination('orders', default_scheme='rabbitmq')
        assert scheme == 'rabbitmq'
        assert queue == 'orders'

    @staticmethod
    def test_bare_uri_no_default_raises() -> None:
        with pytest.raises(ImproperlyConfiguredError):
            split_destination('orders', default_scheme=None)

    @staticmethod
    @pytest.mark.parametrize('uri', ['rabbitmq://', '://orders', ''])
    def test_missing_scheme_or_queue_raises(uri: str) -> None:
        with pytest.raises(ImproperlyConfiguredError):
            split_destination(uri, default_scheme='rabbitmq')


class TestTransportRegistry:
    @staticmethod
    def test_explicit_scheme_resolves() -> None:
        t = StubTransport()
        reg = TransportRegistry({'rabbitmq': t})
        assert reg.sender_for('rabbitmq://orders') is t

    @staticmethod
    def test_bare_uri_uses_explicit_default() -> None:
        t = StubTransport()
        reg = TransportRegistry({'rabbitmq': t}, default_scheme='rabbitmq')
        assert reg.listener_for('orders') is t

    @staticmethod
    def test_sole_transport_is_implicit_default() -> None:
        t = StubTransport()
        reg = TransportRegistry({'rabbitmq': t})
        assert reg.sender_for('orders') is t

    @staticmethod
    def test_unknown_scheme_raises() -> None:
        t = StubTransport()
        with pytest.raises(ImproperlyConfiguredError):
            TransportRegistry({'rabbitmq': t}).sender_for('kafka://x')

    @staticmethod
    def test_bare_uri_multiple_transports_no_default_raises() -> None:
        t1, t2 = StubTransport(), StubTransport()
        reg = TransportRegistry({'rabbitmq': t1, 'kafka': t2})
        with pytest.raises(ImproperlyConfiguredError):
            reg.sender_for('orders')

    @staticmethod
    def test_transports_returns_registered_values() -> None:
        t1, t2 = StubTransport(), StubTransport()
        reg = TransportRegistry({'rabbitmq': t1, 'kafka': t2})
        assert set(reg.transports()) == {t1, t2}


class TestResolveDefaultScheme:
    @staticmethod
    def test_sole_transport_is_default() -> None:
        assert resolve_default_scheme(['rabbitmq']) == 'rabbitmq'

    @staticmethod
    def test_multiple_is_none() -> None:
        assert resolve_default_scheme(['rabbitmq', 'kafka']) is None

    @staticmethod
    def test_empty_is_none() -> None:
        assert resolve_default_scheme([]) is None

    @staticmethod
    def test_explicit_overrides() -> None:
        assert resolve_default_scheme(['rabbitmq', 'kafka'], explicit='kafka') == 'kafka'
