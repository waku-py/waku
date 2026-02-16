from __future__ import annotations

import pytest

from waku.eventsourcing.snapshot.strategy import EventCountStrategy


def test_should_snapshot_at_threshold() -> None:
    strategy = EventCountStrategy(threshold=10)
    assert strategy.should_snapshot(version=10, events_since_snapshot=10) is True


def test_should_not_snapshot_below_threshold() -> None:
    strategy = EventCountStrategy(threshold=10)
    assert strategy.should_snapshot(version=9, events_since_snapshot=9) is False


def test_should_snapshot_above_threshold() -> None:
    strategy = EventCountStrategy(threshold=10)
    assert strategy.should_snapshot(version=50, events_since_snapshot=15) is True


def test_default_threshold_is_100() -> None:
    strategy = EventCountStrategy()
    assert strategy.should_snapshot(version=99, events_since_snapshot=99) is False
    assert strategy.should_snapshot(version=100, events_since_snapshot=100) is True


def test_invalid_threshold_raises() -> None:
    with pytest.raises(ValueError, match='at least 1'):
        EventCountStrategy(threshold=0)

    with pytest.raises(ValueError, match='at least 1'):
        EventCountStrategy(threshold=-5)
