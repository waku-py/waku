from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'CircuitBreakerConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitBreakerConfig:
    """Per-endpoint circuit breaker tuning (rate-based, Wolverine-aligned defaults).

    Trips when, over `tracking_period`, at least `minimum_throughput` messages were recorded AND the
    failure fraction meets or exceeds `failure_rate_threshold` (a fraction in the half-open interval (0.0, 1.0]).
    On trip the endpoint's processing is paused for `pause_time`, then resumed and re-sampled (no
    half-open probe). `track_exceptions` (empty = all) / `ignore_exceptions` filter which exception
    types count as failures. The whole `tracking_period` is a single window — Wolverine's 250 ms
    sub-period sampling smoothing is a documented divergence (not implemented).
    """

    failure_rate_threshold: float = 0.15
    tracking_period: timedelta = timedelta(minutes=10)
    minimum_throughput: int = 10
    pause_time: timedelta = timedelta(minutes=5)
    track_exceptions: tuple[type[Exception], ...] = ()
    ignore_exceptions: tuple[type[Exception], ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 < self.failure_rate_threshold <= 1.0:
            msg = f'failure_rate_threshold must be in (0.0, 1.0], got {self.failure_rate_threshold}'
            raise ImproperlyConfiguredError(msg)
        if self.minimum_throughput < 1:
            msg = f'minimum_throughput must be >= 1, got {self.minimum_throughput}'
            raise ImproperlyConfiguredError(msg)
        if self.tracking_period <= timedelta(0):
            msg = f'tracking_period must be positive, got {self.tracking_period}'
            raise ImproperlyConfiguredError(msg)
        if self.pause_time <= timedelta(0):
            msg = f'pause_time must be positive, got {self.pause_time}'
            raise ImproperlyConfiguredError(msg)
