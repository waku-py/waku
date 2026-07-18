from __future__ import annotations

from datetime import timedelta

import pytest

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import DeadLetterConfig, LeadershipConfig, MessagingConfig


class TestDeadLetterConfigPolling:
    @staticmethod
    def test_polling_defaults_preserve_worker_cadence() -> None:
        config = DeadLetterConfig()
        assert config.polling.poll_interval_min_seconds == 1.0
        assert config.polling.poll_interval_max_seconds == 30.0
        assert config.polling.poll_interval_step_seconds == 1.0
        assert config.polling.poll_interval_jitter_factor == 0.1
        assert config.stop_timeout == timedelta(seconds=10)


class TestDeadLetterConfigValidation:
    @staticmethod
    @pytest.mark.parametrize('batch_size', [0, -1])
    def test_batch_size_must_be_positive(batch_size: int) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'DeadLetterConfig\.batch_size must be >= 1'):
            DeadLetterConfig(batch_size=batch_size)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_cleanup_interval_must_be_positive(value: timedelta) -> None:
        with pytest.raises(
            ImproperlyConfiguredError,
            match=r'DeadLetterConfig\.cleanup_interval must be positive',
        ):
            DeadLetterConfig(cleanup_interval=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_stop_timeout_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'DeadLetterConfig\.stop_timeout must be positive'):
            DeadLetterConfig(stop_timeout=value)


class TestLeadershipConfig:
    @staticmethod
    def test_leadership_defaults_none() -> None:
        assert MessagingConfig().leadership is None

    @staticmethod
    def test_leadership_config_uses_reserved_role() -> None:
        # Lease timing is backend-owned (SqlAlchemyBackend/MemoryBackend register(lease_config=)), so the
        # coordinator config carries only the role and stop timeout — no lease knob.
        config = LeadershipConfig()
        assert config.role == 'waku:leader'
        assert config.stop_timeout == timedelta(seconds=10)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_stop_timeout_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'LeadershipConfig\.stop_timeout must be positive'):
            LeadershipConfig(stop_timeout=value)


class TestMessagingConfigMappingImmutability:
    @staticmethod
    def test_default_transports_reject_item_assignment() -> None:
        config = MessagingConfig()
        with pytest.raises(TypeError):
            config.transports['x'] = None  # type: ignore[index]

    @staticmethod
    def test_default_message_identities_reject_item_assignment() -> None:
        config = MessagingConfig()
        with pytest.raises(TypeError):
            config.message_identities['x'] = None  # type: ignore[index]

    @staticmethod
    def test_default_audited_members_reject_item_assignment() -> None:
        config = MessagingConfig()
        with pytest.raises(TypeError):
            config.audited_members['x'] = None  # type: ignore[index]
