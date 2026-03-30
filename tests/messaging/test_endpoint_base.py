from __future__ import annotations

import math

from waku.messaging.endpoints.base import (
    ExternalEntry,
    LocalQueueEntry,
    external_endpoint,
    local_queue,
)


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
