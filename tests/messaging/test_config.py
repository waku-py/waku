from __future__ import annotations

from datetime import timedelta

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
