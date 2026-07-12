from __future__ import annotations

from dataclasses import dataclass

from waku._internal.polling import PollingConfig
from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'LeaseConfig',
    'PollingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class LeaseConfig:
    ttl_seconds: float = 30.0
    renew_interval_factor: float = 1 / 3

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            msg = f'LeaseConfig.ttl_seconds must be positive, got {self.ttl_seconds}'
            raise ImproperlyConfiguredError(msg)
        if not 0 < self.renew_interval_factor < 1:
            msg = (
                f'LeaseConfig.renew_interval_factor must be in (0, 1) so the lease renews '
                f'strictly before it expires, got {self.renew_interval_factor}'
            )
            raise ImproperlyConfiguredError(msg)

    @property
    def renew_interval_seconds(self) -> float:
        return self.ttl_seconds * self.renew_interval_factor
