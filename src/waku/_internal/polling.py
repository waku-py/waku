from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'DEFAULT_POLLING_CONFIG',
    'PollingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingConfig:
    poll_interval_min_seconds: float = 0.5
    poll_interval_max_seconds: float = 5.0
    poll_interval_step_seconds: float = 1.0
    poll_interval_jitter_factor: float = 0.1

    def __post_init__(self) -> None:
        if not math.isfinite(self.poll_interval_min_seconds) or self.poll_interval_min_seconds <= 0:
            msg = (
                'PollingConfig.poll_interval_min_seconds must be finite and > 0, '
                f'got {self.poll_interval_min_seconds!r}'
            )
            raise ImproperlyConfiguredError(msg)
        if (
            not math.isfinite(self.poll_interval_max_seconds)
            or self.poll_interval_max_seconds < self.poll_interval_min_seconds
        ):
            msg = (
                'PollingConfig.poll_interval_max_seconds must be finite and >= '
                f'poll_interval_min_seconds ({self.poll_interval_min_seconds!r}), '
                f'got {self.poll_interval_max_seconds!r}'
            )
            raise ImproperlyConfiguredError(msg)
        if not math.isfinite(self.poll_interval_step_seconds) or self.poll_interval_step_seconds < 0:
            msg = (
                'PollingConfig.poll_interval_step_seconds must be finite and >= 0, '
                f'got {self.poll_interval_step_seconds!r}'
            )
            raise ImproperlyConfiguredError(msg)
        if not math.isfinite(self.poll_interval_jitter_factor) or not 0 <= self.poll_interval_jitter_factor < 1:
            msg = (
                'PollingConfig.poll_interval_jitter_factor must be finite and in [0, 1), '
                f'got {self.poll_interval_jitter_factor!r}'
            )
            raise ImproperlyConfiguredError(msg)
        jittered_minimum = self.poll_interval_min_seconds * (1 - self.poll_interval_jitter_factor)
        if jittered_minimum <= 0:
            msg = (
                'PollingConfig jittered minimum must remain > 0 for '
                f'poll_interval_min_seconds={self.poll_interval_min_seconds!r} and '
                f'poll_interval_jitter_factor={self.poll_interval_jitter_factor!r}, '
                f'got {jittered_minimum!r}'
            )
            raise ImproperlyConfiguredError(msg)
        jittered_maximum = self.poll_interval_max_seconds * (1 + self.poll_interval_jitter_factor)
        if not math.isfinite(jittered_maximum):
            msg = (
                'PollingConfig jittered maximum must remain finite for '
                f'poll_interval_max_seconds={self.poll_interval_max_seconds!r} and '
                f'poll_interval_jitter_factor={self.poll_interval_jitter_factor!r}, '
                f'got {jittered_maximum!r}'
            )
            raise ImproperlyConfiguredError(msg)


DEFAULT_POLLING_CONFIG: Final = PollingConfig()
