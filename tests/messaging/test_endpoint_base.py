from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from waku._internal.sentinel import MISSING  # noqa: PLC2701
from waku.messaging.circuit_breaker import CircuitBreakerConfig
from waku.messaging.endpoints.base import (
    EndpointMode,
    ExternalEntry,
    InboundEntry,
    LocalQueueEntry,
    external_endpoint,
    listen,
    local_queue,
)
from waku.messaging.sending import SendingFailurePolicy
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage


class TestEndpointEntryFactories:
    @staticmethod
    def test_local_queue_creates_entry_with_defaults() -> None:
        entry = local_queue('q://test')
        assert isinstance(entry, LocalQueueEntry)
        assert entry.uri == 'q://test'
        assert entry.stop_timeout == 5.0
        assert entry.max_buffer_size == math.inf

    @staticmethod
    def test_local_queue_with_custom_timeout() -> None:
        entry = local_queue('q://test', stop_timeout=10.0)
        assert entry.stop_timeout == 10.0

    @staticmethod
    def test_local_queue_with_custom_buffer_size() -> None:
        entry = local_queue('q://test', max_buffer_size=100)
        assert entry.max_buffer_size == 100

    @staticmethod
    def test_external_endpoint_creates_entry() -> None:
        entry = external_endpoint('ext://bus')
        assert isinstance(entry, ExternalEntry)
        assert entry.uri == 'ext://bus'


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


class TestExternalEntryPartitionBy:
    @staticmethod
    def test_default_partition_by_is_none() -> None:
        entry = external_endpoint('ext://bus')
        assert entry.partition_by is None

    @staticmethod
    def test_accepts_partition_by_callable() -> None:
        def strategy(_: IMessage) -> str | None:
            return 'account-42'  # pragma: no cover

        entry = external_endpoint('ext://bus', partition_by=strategy)
        assert entry.partition_by is strategy


class TestExternalEntrySendingFailurePolicies:
    @staticmethod
    def test_default_sending_failure_policies_is_empty() -> None:
        entry = external_endpoint('ext://bus')
        assert entry.sending_failure_policies == ()

    @staticmethod
    def test_external_endpoint_carries_sending_failure_policies() -> None:
        policy = (
            SendingFailurePolicy
            .on_exception(ConnectionError)
            .retry_with_backoff(max_attempts=3)
            .then_move_to_dead_letter()
        )
        entry = external_endpoint('amqp://orders', sending_failure_policies=[policy])
        assert entry.sending_failure_policies == (policy,)


class _StubMapper(IEnvelopeMapper[Any, Any]):
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> Any:  # noqa: ARG002, PLR6301
        return payload

    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raise NotImplementedError


class TestInboundEntryMapper:
    @staticmethod
    def test_inbound_entry_mapper_defaults_to_none() -> None:
        entry = InboundEntry(uri='amqp://orders')
        assert entry.mapper is None

    @staticmethod
    def test_inbound_entry_accepts_mapper() -> None:
        stub = _StubMapper()
        entry = InboundEntry(uri='amqp://orders', mapper=stub)
        assert entry.mapper is stub

    @staticmethod
    def test_listen_mapper_defaults_to_none() -> None:
        entry = listen('amqp://orders')
        assert entry.mapper is None

    @staticmethod
    def test_listen_carries_mapper_to_inbound_entry() -> None:
        stub = _StubMapper()
        entry = listen('amqp://orders', mapper=stub)
        assert entry.mapper is stub


class TestExternalEntryMapper:
    @staticmethod
    def test_external_entry_mapper_defaults_to_none() -> None:
        entry = ExternalEntry(uri='amqp://orders')
        assert entry.mapper is None

    @staticmethod
    def test_external_entry_accepts_mapper() -> None:
        stub = _StubMapper()
        entry = ExternalEntry(uri='amqp://orders', mapper=stub)
        assert entry.mapper is stub

    @staticmethod
    def test_external_endpoint_mapper_defaults_to_none() -> None:
        entry = external_endpoint('amqp://orders')
        assert entry.mapper is None

    @staticmethod
    def test_external_endpoint_carries_mapper_to_external_entry() -> None:
        stub = _StubMapper()
        entry = external_endpoint('amqp://orders', mapper=stub)
        assert entry.mapper is stub
