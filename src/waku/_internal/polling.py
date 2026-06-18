from dataclasses import dataclass

__all__ = [
    'PollingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingConfig:
    poll_interval_min_seconds: float = 0.5
    poll_interval_max_seconds: float = 5.0
    poll_interval_step_seconds: float = 1.0
    poll_interval_jitter_factor: float = 0.1
