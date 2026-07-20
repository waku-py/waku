from __future__ import annotations

import math
from datetime import timedelta

import pytest

from waku import ImproperlyConfiguredError
from waku.messaging import CircuitBreakerConfig


def test_default_config_matches_wolverine_defaults() -> None:
    config = CircuitBreakerConfig()
    assert config.failure_rate_threshold == 0.15
    assert config.tracking_period == timedelta(minutes=10)
    assert config.minimum_throughput == 10
    assert config.pause_time == timedelta(minutes=5)
    assert config.track_exceptions == ()
    assert config.ignore_exceptions == ()


@pytest.mark.parametrize('bad_rate', [0.0, -0.1, 1.5, math.inf, -math.inf, math.nan])
def test_failure_rate_threshold_must_be_in_unit_interval(bad_rate: float) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='failure_rate_threshold'):
        CircuitBreakerConfig(failure_rate_threshold=bad_rate)


def test_minimum_throughput_must_be_positive() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='minimum_throughput'):
        CircuitBreakerConfig(minimum_throughput=0)


def test_tracking_period_must_be_positive() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='tracking_period'):
        CircuitBreakerConfig(tracking_period=timedelta(0))


def test_pause_time_must_be_positive() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='pause_time'):
        CircuitBreakerConfig(pause_time=timedelta(0))
