from __future__ import annotations

from datetime import timedelta

import pytest

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.inbox.config import InboxConfig


class TestInboxConfig:
    @staticmethod
    def test_defaults() -> None:
        config = InboxConfig()
        assert config.keep_after_handled == timedelta(minutes=5)
        assert config.recovery_interval == timedelta(minutes=1)
        assert config.stop_timeout == timedelta(seconds=10)

    @staticmethod
    def test_drain_defaults() -> None:
        config = InboxConfig()
        assert config.batch_size == 100
        assert config.max_drain_attempts == 5

    @staticmethod
    @pytest.mark.parametrize('batch_size', [0, -1])
    def test_batch_size_must_be_positive(batch_size: int) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'InboxConfig\.batch_size must be >= 1'):
            InboxConfig(batch_size=batch_size)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_recovery_interval_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'InboxConfig\.recovery_interval must be positive'):
            InboxConfig(recovery_interval=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_scheduled_poll_interval_must_be_positive(value: timedelta) -> None:
        with pytest.raises(
            ImproperlyConfiguredError,
            match=r'InboxConfig\.scheduled_poll_interval must be positive',
        ):
            InboxConfig(scheduled_poll_interval=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_stop_timeout_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'InboxConfig\.stop_timeout must be positive'):
            InboxConfig(stop_timeout=value)
