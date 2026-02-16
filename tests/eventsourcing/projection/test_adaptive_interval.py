from __future__ import annotations

from waku.eventsourcing.projection.adaptive_interval import AdaptiveInterval, calculate_backoff_with_jitter


def test_initial_value_is_min() -> None:
    interval = AdaptiveInterval(min_seconds=1.0, max_seconds=10.0, step_seconds=2.0)
    assert interval.current == 1.0


def test_on_idle_increases_by_step() -> None:
    interval = AdaptiveInterval(min_seconds=1.0, max_seconds=10.0, step_seconds=2.0)
    interval.on_idle()
    assert interval.current == 3.0


def test_on_idle_caps_at_max() -> None:
    interval = AdaptiveInterval(min_seconds=1.0, max_seconds=5.0, step_seconds=3.0)
    interval.on_idle()  # 4.0
    interval.on_idle()  # 5.0 (capped)
    assert interval.current == 5.0
    interval.on_idle()  # still 5.0
    assert interval.current == 5.0


def test_on_work_done_resets_to_min() -> None:
    interval = AdaptiveInterval(min_seconds=1.0, max_seconds=10.0, step_seconds=2.0)
    interval.on_idle()
    interval.on_idle()
    assert interval.current > 1.0
    interval.on_work_done()
    assert interval.current == 1.0


def test_current_with_jitter_within_bounds() -> None:
    interval = AdaptiveInterval(min_seconds=5.0, max_seconds=10.0, step_seconds=1.0, jitter_factor=0.1)
    for _ in range(100):
        jittered = interval.current_with_jitter()
        assert 5.0 * 0.9 <= jittered <= 5.0 * 1.1


def test_backoff_attempt_zero_within_base() -> None:
    for _ in range(100):
        delay = calculate_backoff_with_jitter(attempt=0, base_delay_seconds=1.0, max_delay_seconds=60.0)
        assert 0 <= delay <= 1.0


def test_backoff_grows_exponentially() -> None:
    samples_at_1 = [
        calculate_backoff_with_jitter(attempt=1, base_delay_seconds=1.0, max_delay_seconds=60.0) for _ in range(200)
    ]
    samples_at_3 = [
        calculate_backoff_with_jitter(attempt=3, base_delay_seconds=1.0, max_delay_seconds=60.0) for _ in range(200)
    ]
    assert max(samples_at_1) <= 2.0
    assert max(samples_at_3) <= 8.0
    # On average, higher attempts produce larger delays
    avg_1 = sum(samples_at_1) / len(samples_at_1)
    avg_3 = sum(samples_at_3) / len(samples_at_3)
    assert avg_3 > avg_1


def test_backoff_capped_at_max() -> None:
    for _ in range(100):
        delay = calculate_backoff_with_jitter(attempt=20, base_delay_seconds=1.0, max_delay_seconds=10.0)
        assert 0 <= delay <= 10.0
