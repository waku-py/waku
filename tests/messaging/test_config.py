from __future__ import annotations

from datetime import timedelta

from waku.messaging import DeadLetterConfig


class TestDeadLetterConfigPolling:
    @staticmethod
    def test_polling_defaults_preserve_worker_cadence() -> None:
        config = DeadLetterConfig()
        assert config.polling.poll_interval_min_seconds == 1.0
        assert config.polling.poll_interval_max_seconds == 30.0
        assert config.polling.poll_interval_step_seconds == 1.0
        assert config.polling.poll_interval_jitter_factor == 0.1
        assert config.stop_timeout == timedelta(seconds=10)
