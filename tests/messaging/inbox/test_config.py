from __future__ import annotations

import os
import socket
from datetime import timedelta

import pytest

from waku.messaging.inbox.config import InboxConfig

from tests.messaging.inbox.fake_store import FakeInboxStore


class TestInboxConfig:
    @staticmethod
    def test_defaults() -> None:
        config = InboxConfig(store=FakeInboxStore)
        assert config.keep_after_handled == timedelta(minutes=5)
        assert config.stuck_threshold == timedelta(minutes=5)
        assert config.recovery_interval == timedelta(minutes=1)
        assert not config.owner_id
        assert config.stop_timeout == pytest.approx(10.0)

    @staticmethod
    def test_resolve_owner_id_auto_generates_hostname_pid() -> None:
        config = InboxConfig(store=FakeInboxStore)
        resolved = config.resolve_owner_id()
        expected = f'{socket.gethostname()}:{os.getpid()}'
        assert resolved == expected

    @staticmethod
    def test_resolve_owner_id_preserves_explicit_value() -> None:
        config = InboxConfig(store=FakeInboxStore, owner_id='worker-a')
        assert config.resolve_owner_id() == 'worker-a'

    @staticmethod
    def test_drain_defaults() -> None:
        config = InboxConfig(store=FakeInboxStore)
        assert config.batch_size == 100
        assert config.max_drain_attempts == 5
