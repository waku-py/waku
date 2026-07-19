from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    'BufferingLimits',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class BufferingLimits:
    """In-memory watermark for inbound listener backpressure: stop the listener at ``high``, resume at ``low``."""

    high: int
    low: int

    def __post_init__(self) -> None:
        if not 0 <= self.low < self.high:
            msg = f'BufferingLimits requires 0 <= low < high, got low={self.low}, high={self.high}'
            raise ValueError(msg)
