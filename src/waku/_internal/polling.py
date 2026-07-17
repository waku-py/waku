from __future__ import annotations

from dataclasses import dataclass
from typing import Final

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


DEFAULT_POLLING_CONFIG: Final = PollingConfig()
