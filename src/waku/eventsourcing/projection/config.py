from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    'CatchUpProjectionConfig',
    'LeaseConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class CatchUpProjectionConfig:
    batch_size: int = 100
    max_attempts: int = 3
    base_retry_delay_seconds: float = 10.0
    max_retry_delay_seconds: float = 300.0
    poll_interval_min_seconds: float = 0.5
    poll_interval_max_seconds: float = 5.0
    poll_interval_step_seconds: float = 1.0
    poll_interval_jitter_factor: float = 0.1


@dataclass(frozen=True, slots=True, kw_only=True)
class LeaseConfig:
    ttl_seconds: float = 30.0
    renew_interval_factor: float = 1 / 3

    @property
    def renew_interval_seconds(self) -> float:
        return self.ttl_seconds * self.renew_interval_factor
