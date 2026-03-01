from __future__ import annotations

from waku.eventsourcing.projection.gap_detection import GapTracker


def test_no_gaps_returns_max_position() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)
    safe = tracker.safe_position(checkpoint=4, committed_positions=[5, 6, 7, 8])
    assert safe == 8


def test_gap_at_start_blocks_all() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 7, 8])
    assert safe == 4  # can't advance past gap at 5


def test_gap_in_middle_stops_at_gap() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)
    safe = tracker.safe_position(checkpoint=4, committed_positions=[5, 6, 8, 9])
    assert safe == 6  # gap at 7 stops advancement


def test_empty_positions_returns_checkpoint() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)
    safe = tracker.safe_position(checkpoint=4, committed_positions=[])
    assert safe == 4


def test_gap_expires_after_timeout() -> None:
    fake_time = 0.0
    tracker = GapTracker(gap_timeout_seconds=5.0, clock=lambda: fake_time)

    # First call: detects gap at 5, blocks
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 7, 8])
    assert safe == 4

    # Second call: not enough time passed, still blocked
    fake_time = 4.9
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 7, 8])
    assert safe == 4

    # Third call: gap expired, skip it
    fake_time = 5.0
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 7, 8])
    assert safe == 8


def test_gap_fills_before_timeout() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)

    # First call: gap at 5
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 7, 8])
    assert safe == 4

    # Second call: gap filled
    safe = tracker.safe_position(checkpoint=4, committed_positions=[5, 6, 7, 8])
    assert safe == 8
    assert len(tracker.known_gaps) == 0  # gap cleaned up


def test_multiple_gaps() -> None:
    tracker = GapTracker(gap_timeout_seconds=10.0)

    # Gaps at 5 and 7
    safe = tracker.safe_position(checkpoint=4, committed_positions=[6, 8, 9])
    assert safe == 4  # blocked by first gap


def test_expired_gaps_are_cleaned_up() -> None:
    fake_time = 0.0
    tracker = GapTracker(gap_timeout_seconds=5.0, clock=lambda: fake_time)

    tracker.safe_position(checkpoint=4, committed_positions=[6, 7])
    assert 5 in tracker.known_gaps

    fake_time = 5.0
    tracker.safe_position(checkpoint=4, committed_positions=[6, 7])
    assert 5 not in tracker.known_gaps  # cleaned up after expiry
