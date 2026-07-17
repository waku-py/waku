from __future__ import annotations

import pytest

from waku._internal.lease import LeaseConfig
from waku.eventsourcing.projection.config import PollingConfig
from waku.exceptions import ImproperlyConfiguredError


def test_catch_up_config_defaults() -> None:
    config = PollingConfig()
    assert config.poll_interval_min_seconds == 0.5
    assert config.poll_interval_max_seconds == 5.0
    assert config.poll_interval_step_seconds == 1.0
    assert config.poll_interval_jitter_factor == 0.1


def test_lease_config_renew_interval() -> None:
    config = LeaseConfig()
    assert config.renew_interval_seconds == pytest.approx(10.0)


def test_lease_config_custom_ttl() -> None:
    config = LeaseConfig(ttl_seconds=60.0)
    assert config.renew_interval_seconds == pytest.approx(20.0)


@pytest.mark.parametrize('factor', [1.0, 1.5])
def test_lease_config_rejects_factor_ge_one(factor: float) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='renew_interval_factor'):
        LeaseConfig(renew_interval_factor=factor)


@pytest.mark.parametrize('factor', [0.0, -0.1])
def test_lease_config_rejects_non_positive_factor(factor: float) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='renew_interval_factor'):
        LeaseConfig(renew_interval_factor=factor)


def test_lease_config_rejects_non_positive_ttl() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='ttl_seconds'):
        LeaseConfig(ttl_seconds=0.0)


def test_lease_config_accepts_valid_factor() -> None:
    config = LeaseConfig(ttl_seconds=30.0, renew_interval_factor=1 / 3)

    assert config.renew_interval_seconds < config.ttl_seconds
