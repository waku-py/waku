from __future__ import annotations

from waku.eventsourcing.snapshot.interfaces import ISnapshotStrategy

__all__ = ['EventCountStrategy']


class EventCountStrategy(ISnapshotStrategy):
    def __init__(self, threshold: int = 100) -> None:
        if threshold < 1:
            msg = f'Threshold must be at least 1, got {threshold}'
            raise ValueError(msg)
        self._threshold = threshold

    def should_snapshot(self, version: int, events_since_snapshot: int) -> bool:  # noqa: ARG002
        return events_since_snapshot >= self._threshold
