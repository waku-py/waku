from __future__ import annotations

from dataclasses import dataclass

from waku._internal.polling import PollingConfig

__all__ = [
    'LeaseConfig',
    'PollingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class LeaseConfig:
    ttl_seconds: float = 30.0
    renew_interval_factor: float = 1 / 3

    @property
    def renew_interval_seconds(self) -> float:
        return self.ttl_seconds * self.renew_interval_factor
