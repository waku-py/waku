from __future__ import annotations

from dataclasses import dataclass

from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'MessageIdentity',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageIdentity:
    name: str
    version: int = 1

    def __post_init__(self) -> None:
        if not self.name:
            msg = 'name must be non-empty'
            raise ImproperlyConfiguredError(msg)
        if self.version < 1:
            msg = 'version must be >= 1'
            raise ImproperlyConfiguredError(msg)

    def __str__(self) -> str:
        if self.version == 1:
            return self.name
        return f'{self.name}.v{self.version}'
