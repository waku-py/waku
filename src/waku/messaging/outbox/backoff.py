from __future__ import annotations

import random

__all__ = [
    'calculate_backoff',
]


def calculate_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    return random.uniform(0, min(base_delay * 2**attempt, max_delay))  # noqa: S311
