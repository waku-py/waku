from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from waku.eventsourcing.projection.config import LeaseConfig, PollingConfig


def test_catch_up_config_defaults() -> None:
    config = PollingConfig()
    assert config.poll_interval_min_seconds == 0.5
    assert config.poll_interval_max_seconds == 5.0
    assert config.poll_interval_step_seconds == 1.0
    assert config.poll_interval_jitter_factor == 0.1


def test_catch_up_config_immutable() -> None:
    config = PollingConfig()
    with pytest.raises(FrozenInstanceError):
        config.poll_interval_min_seconds = 1.0  # type: ignore[misc]


def test_lease_config_renew_interval() -> None:
    config = LeaseConfig()
    assert config.renew_interval_seconds == pytest.approx(10.0)


def test_lease_config_custom_ttl() -> None:
    config = LeaseConfig(ttl_seconds=60.0)
    assert config.renew_interval_seconds == pytest.approx(20.0)
