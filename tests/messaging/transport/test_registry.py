from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.transport._internal.registry import TransportRegistry, resolve_default_scheme, split_destination
from waku.messaging.transport.faststream import (
    DefaultKafkaEnvelopeMapper,
    DefaultRabbitEnvelopeMapper,
    IRabbitEnvelopeMapper,
)
from waku.messaging.transport.interfaces import IEnvelopeMapper, ITransport

from tests.messaging.helpers import StubSubscription

if TYPE_CHECKING:
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import EnvelopeMetadata, Subscription


class StubTransport(ITransport):
    """Minimal ITransport double — no-op on all methods."""

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        pass  # pragma: no cover

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
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


class _StubMapper(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: Any) -> Any:
        raise NotImplementedError  # pragma: no cover

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], Any]:
        raise NotImplementedError  # pragma: no cover


class _RabbitFamilyTransport(StubTransport):
    """Transport double narrowing its accepted mapper family, like FastStreamRabbitTransport."""

    mapper_family = IRabbitEnvelopeMapper


class TestTransportRegistryMapperFamilyValidation:
    @staticmethod
    def test_wrong_family_mapper_rejected_at_construction() -> None:
        with pytest.raises(ImproperlyConfiguredError) as exc_info:
            TransportRegistry(
                {'rabbitmq': _RabbitFamilyTransport()},
                external_mappers={'rabbitmq://orders': DefaultKafkaEnvelopeMapper()},
            )
        message = str(exc_info.value)
        assert 'rabbitmq://orders' in message
        assert 'DefaultKafkaEnvelopeMapper' in message
        assert 'IRabbitEnvelopeMapper' in message

    @staticmethod
    def test_matching_family_mapper_accepted() -> None:
        mapper = DefaultRabbitEnvelopeMapper()
        reg = TransportRegistry(
            {'rabbitmq': _RabbitFamilyTransport()},
            external_mappers={'rabbitmq://orders': mapper},
        )
        assert reg.mapper_for('rabbitmq://orders') is mapper

    @staticmethod
    def test_transport_without_declared_family_accepts_any_mapper() -> None:
        # A custom/test transport that does not narrow mapper_family inherits the permissive
        # IEnvelopeMapper base — any mapper is accepted (no false positives).
        mapper = DefaultKafkaEnvelopeMapper()
        reg = TransportRegistry(
            {'rabbitmq': StubTransport()},
            external_mappers={'rabbitmq://orders': mapper},
        )
        assert reg.mapper_for('rabbitmq://orders') is mapper


class TestTransportRegistryMapperFor:
    @staticmethod
    def test_configured_uri_returns_override() -> None:
        mapper = _StubMapper()
        reg = TransportRegistry(
            {'rabbitmq': StubTransport()},
            external_mappers={'rabbitmq://orders': mapper},
        )
        assert reg.mapper_for('rabbitmq://orders') is mapper

    @staticmethod
    def test_unconfigured_uri_returns_none() -> None:
        mapper = _StubMapper()
        reg = TransportRegistry(
            {'rabbitmq': StubTransport()},
            external_mappers={'rabbitmq://orders': mapper},
        )
        assert reg.mapper_for('rabbitmq://other') is None

    @staticmethod
    def test_no_external_mappers_always_returns_none() -> None:
        reg = TransportRegistry({'rabbitmq': StubTransport()})
        assert reg.mapper_for('rabbitmq://orders') is None
