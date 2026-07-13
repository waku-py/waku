from __future__ import annotations

from datetime import timedelta

from waku.messaging import DeadLetterConfig, LeadershipConfig, LeaseConfig, MessagingConfig


class TestDeadLetterConfigPolling:
    @staticmethod
    def test_polling_defaults_preserve_worker_cadence() -> None:
        config = DeadLetterConfig()
        assert config.polling.poll_interval_min_seconds == 1.0
        assert config.polling.poll_interval_max_seconds == 30.0
        assert config.polling.poll_interval_step_seconds == 1.0
        assert config.polling.poll_interval_jitter_factor == 0.1
        assert config.stop_timeout == timedelta(seconds=10)


class TestLeadershipConfig:
    @staticmethod
    def test_leadership_defaults_none() -> None:
        assert MessagingConfig().leadership is None

    @staticmethod
    def test_leadership_config_composes_lease_and_reserved_role() -> None:
        config = LeadershipConfig()
        assert isinstance(config.lease, LeaseConfig)
        assert config.role == 'waku:leader'
        assert config.stop_timeout == timedelta(seconds=10)

    @staticmethod
    def test_leadership_lease_is_tunable() -> None:
        config = LeadershipConfig(lease=LeaseConfig(ttl_seconds=0.5))
        assert config.lease.ttl_seconds == 0.5
        assert config.lease.renew_interval_seconds == 0.5 / 3
