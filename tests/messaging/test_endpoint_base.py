from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku._internal.sentinel import MISSING
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.endpoints.base import BrokerEndpointEntry, EndpointMode, LocalQueueEntry
from waku.messaging.inbox.backpressure import BufferingLimits
from waku.messaging.observability.observer import IMessageObserver
from waku.messaging.router import external_endpoint, listen, local_queue
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper

if TYPE_CHECKING:
    from waku.messages import IMessage


class _ObserverA(IMessageObserver):
    pass


class TestEndpointEntryFactories:
    @staticmethod
    def test_local_queue_creates_entry_with_defaults() -> None:
        entry = local_queue('q://test')
        assert isinstance(entry, LocalQueueEntry)
        assert entry.uri == 'q://test'
        assert entry.stop_timeout == timedelta(seconds=5)
        assert entry.max_buffer_size == math.inf

    @staticmethod
    def test_local_queue_with_custom_timeout() -> None:
        entry = local_queue('q://test', stop_timeout=timedelta(seconds=10.0))
        assert entry.stop_timeout == timedelta(seconds=10)

    @staticmethod
    def test_local_queue_with_custom_buffer_size() -> None:
        entry = local_queue('q://test', max_buffer_size=100)
        assert entry.max_buffer_size == 100

    @staticmethod
    def test_external_endpoint_creates_entry() -> None:
        entry = external_endpoint('ext://bus')
        assert isinstance(entry, BrokerEndpointEntry)
        assert entry.uri == 'ext://bus'
        assert entry.listen is None


class TestLocalQueueNewFields:
    @staticmethod
    def test_defaults_to_inherit_mode() -> None:
        entry = local_queue('q://x')
        assert entry.mode is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_default_max_parallel_is_one() -> None:
        entry = local_queue('q://x')
        assert entry.max_parallel == 1

    @staticmethod
    def test_default_partition_by_is_none() -> None:
        entry = local_queue('q://x')
        assert entry.partition_by is None

    @staticmethod
    def test_accepts_inline_mode() -> None:
        entry = local_queue('q://x', mode=EndpointMode.INLINE)
        assert entry.mode == EndpointMode.INLINE

    @staticmethod
    def test_accepts_durable_mode() -> None:
        entry = local_queue('q://x', mode=EndpointMode.DURABLE)
        assert entry.mode == EndpointMode.DURABLE

    @staticmethod
    def test_accepts_custom_max_parallel() -> None:
        entry = local_queue('q://x', max_parallel=8)
        assert entry.max_parallel == 8

    @staticmethod
    def test_accepts_partition_by_callable() -> None:
        def strategy(_: IMessage) -> str | None:
            return 'group-1'  # pragma: no cover

        entry = local_queue('q://x', partition_by=strategy)
        assert entry.partition_by is strategy


class TestLocalQueueCircuitBreaker:
    @staticmethod
    def test_local_queue_carries_circuit_breaker_config() -> None:
        cb = CircuitBreakerConfig(failure_rate_threshold=0.3, pause_time=timedelta(seconds=5))
        entry = local_queue('q', circuit_breaker=cb)
        assert entry.circuit_breaker is cb

    @staticmethod
    def test_local_queue_circuit_breaker_defaults_to_inherit() -> None:
        assert local_queue('q').circuit_breaker is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support


class TestLocalQueueObservers:
    @staticmethod
    def test_default_observers_is_empty_tuple() -> None:
        entry = local_queue('q')
        assert entry.observers == ()

    @staticmethod
    def test_observers_dedup_preserves_first_seen_order() -> None:
        entry = local_queue('q', observers=(_ObserverA, _ObserverA))
        assert entry.observers == (_ObserverA,)


class _StubMapper(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> Any:
        return payload

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raise NotImplementedError


class TestListenBuildsBrokerEndpointEntry:
    @staticmethod
    def test_listen_creates_broker_endpoint_entry_with_listen_aspect() -> None:
        entry = listen('amqp://orders')
        assert isinstance(entry, BrokerEndpointEntry)
        assert entry.listen is not None
        assert entry.send is None

    @staticmethod
    def test_listen_mapper_defaults_to_missing() -> None:
        entry = listen('amqp://orders')
        assert entry.mapper is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_listen_carries_mapper_to_broker_endpoint_entry() -> None:
        stub = _StubMapper()
        entry = listen('amqp://orders', mapper=stub)
        assert entry.mapper is stub

    @staticmethod
    def test_listen_carries_aspect_fields() -> None:
        cb = CircuitBreakerConfig(minimum_throughput=1)
        limits = BufferingLimits(high=100, low=20)
        entry = listen('amqp://orders', max_requeue_attempts=3, circuit_breaker=cb, backpressure=limits)
        assert entry.listen is not None
        assert entry.listen.max_requeue_attempts == 3
        assert entry.listen.circuit_breaker is cb
        assert entry.listen.backpressure is limits


class TestBrokerEndpointEntryDefaults:
    @staticmethod
    def test_mapper_defaults_to_inherit() -> None:
        entry = BrokerEndpointEntry(uri='x')
        assert entry.mapper is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_partition_by_defaults_to_inherit() -> None:
        entry = BrokerEndpointEntry(uri='x')
        assert entry.partition_by is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_listen_defaults_to_none() -> None:
        entry = BrokerEndpointEntry(uri='x')
        assert entry.listen is None

    @staticmethod
    def test_send_defaults_to_none() -> None:
        entry = BrokerEndpointEntry(uri='x')
        assert entry.send is None
