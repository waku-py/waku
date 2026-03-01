from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['GapTracker']


class GapTracker:
    def __init__(
        self,
        gap_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gap_timeout_seconds = gap_timeout_seconds
        self._clock = clock
        self._known_gaps: dict[int, float] = {}

    @property
    def known_gaps(self) -> dict[int, float]:
        return dict(self._known_gaps)

    def safe_position(self, checkpoint: int, committed_positions: list[int]) -> int:
        """Return the highest position the checkpoint can safely advance to.

        Args:
            checkpoint: Current checkpoint position.
            committed_positions: Committed global positions in ascending order.
        """
        if not committed_positions:
            return checkpoint

        max_position = committed_positions[-1]
        position_set = set(committed_positions)
        now = self._clock()
        safe = checkpoint

        pos = checkpoint + 1
        while pos <= max_position:
            if pos in position_set:
                safe = pos
                self._known_gaps.pop(pos, None)
            elif pos in self._known_gaps:
                if now - self._known_gaps[pos] >= self._gap_timeout_seconds:
                    safe = pos
                    del self._known_gaps[pos]
                else:
                    break
            else:
                self._known_gaps[pos] = now
                break
            pos += 1

        if pos > max_position:
            return safe

        # Track remaining gaps so they start their timeout clocks
        for remaining_pos in range(pos + 1, max_position + 1):
            if remaining_pos not in position_set and remaining_pos not in self._known_gaps:
                self._known_gaps[remaining_pos] = now

        return safe
