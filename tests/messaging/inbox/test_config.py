from __future__ import annotations

import os
import socket
from datetime import timedelta

from waku.messaging.inbox.config import InboxConfig


class TestInboxConfig:
    @staticmethod
    def test_defaults() -> None:
        config = InboxConfig()
        assert config.keep_after_handled == timedelta(minutes=5)
        assert config.stuck_threshold == timedelta(minutes=5)
        assert config.recovery_interval == timedelta(minutes=1)
        assert not config.owner_id
        assert config.stop_timeout == timedelta(seconds=10)

    @staticmethod
    def test_resolve_owner_id_auto_generates_hostname_pid() -> None:
        config = InboxConfig()
        resolved = config.resolve_owner_id()
        expected = f'{socket.gethostname()}:{os.getpid()}'
        assert resolved == expected

    @staticmethod
    def test_resolve_owner_id_preserves_explicit_value() -> None:
        config = InboxConfig(owner_id='worker-a')
        assert config.resolve_owner_id() == 'worker-a'

    @staticmethod
    def test_drain_defaults() -> None:
        config = InboxConfig()
        assert config.batch_size == 100
        assert config.max_drain_attempts == 5
