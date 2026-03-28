from __future__ import annotations

from waku.messaging.outbox.backoff import calculate_backoff


class TestCalculateBackoff:
    @staticmethod
    def test_first_attempt_within_base_delay() -> None:
        delay = calculate_backoff(attempt=0, base_delay=1.0, max_delay=60.0)
        assert 0 <= delay <= 1.0

    @staticmethod
    def test_all_delays_within_bounds() -> None:
        delays = [calculate_backoff(attempt=i, base_delay=1.0, max_delay=60.0) for i in range(10)]
        assert all(0 <= d <= 60.0 for d in delays)

    @staticmethod
    def test_respects_max_delay() -> None:
        delay = calculate_backoff(attempt=100, base_delay=1.0, max_delay=30.0)
        assert delay <= 30.0
