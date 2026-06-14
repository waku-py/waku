from __future__ import annotations

import math
from typing import TYPE_CHECKING

from waku.messaging.endpoints.base import (
    EndpointMode,
    ExternalEntry,
    LocalQueueEntry,
    external_endpoint,
    local_queue,
)

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
    def test_defaults_to_buffered_mode() -> None:
        entry = local_queue('q://x')
        assert entry.mode == EndpointMode.BUFFERED

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
