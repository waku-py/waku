from __future__ import annotations

import math
import random

__all__ = [
    'AdaptiveInterval',
    'calculate_backoff_with_jitter',
]


class AdaptiveInterval:
    __slots__ = ('_current', '_jitter_factor', '_max', '_min', '_step')

    def __init__(
        self,
        min_seconds: float,
        max_seconds: float,
        step_seconds: float,
        jitter_factor: float = 0.1,
    ) -> None:
        self._min = min_seconds
        self._max = max_seconds
        self._step = step_seconds
        self._jitter_factor = jitter_factor
        self._current = min_seconds

    @property
    def current(self) -> float:
        return self._current

    def current_with_jitter(self) -> float:
        return self._current * random.uniform(1 - self._jitter_factor, 1 + self._jitter_factor)  # noqa: S311

    def on_work_done(self) -> None:
        self._current = self._min

    def on_idle(self) -> None:
        self._current = min(self._current + self._step, self._max)


def calculate_backoff_with_jitter(
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    if base_delay_seconds <= 0:
        return 0.0
    # Cap the exponent BEFORE exponentiating: an unbounded attempt counter overflows the int->float
    # coercion (2**1024 exceeds float max). max_doublings is the doublings needed to reach max_delay;
    # ceil rounding may overshoot by < 2x, so the outer min() still clamps the product.
    if max_delay_seconds <= base_delay_seconds:
        max_doublings = 0
    else:
        max_doublings = math.ceil(math.log2(max_delay_seconds / base_delay_seconds))
    max_delay = min(base_delay_seconds * (2 ** min(attempt, max_doublings)), max_delay_seconds)
    return random.uniform(0, max_delay)  # noqa: S311
