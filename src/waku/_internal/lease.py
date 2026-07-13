import abc
import contextlib
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, TypeAlias

import anyio

from waku._internal.clock import Now, utc_now
from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'ILease',
    'InMemoryLease',
    'LeaseConfig',
]

# (holder_id, expires_at) — one live holder per lease name at a time.
_Entry: TypeAlias = tuple[str, datetime]


class ILease(abc.ABC):
    """Cross-domain lease: single-owner coordination over an opaque ``name``."""

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        """Yield True while THIS holder owns the lease for ``name``.

        The context-manager body is cancelled when the lease is lost (stolen or expired). Yield
        False immediately if another live holder owns it. On exit (normal or cancelled) the lease
        is released.
        """
        yield False  # pragma: no cover


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


_DEFAULT_CONFIG: Final = LeaseConfig()


class InMemoryLease(ILease):
    """In-process lease with injected-clock expiry, mirroring the PostgreSQL lease shape.

    Default construction gets a private store (single-node isolation). Passing a shared ``store``
    with distinct per-instance ``holder_id`` models contending nodes over one backing table.
    """

    def __init__(
        self,
        config: LeaseConfig = _DEFAULT_CONFIG,
        *,
        store: dict[str, _Entry] | None = None,
        now: Now = utc_now,
    ) -> None:
        self._config = config
        self._store = store if store is not None else {}
        self._now = now
        self._holder_id = str(uuid.uuid4())

    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        entry = self._store.get(name)
        if entry is not None and entry[1] > self._now():
            yield False
            return

        self._store[name] = (self._holder_id, self._now() + timedelta(seconds=self._config.ttl_seconds))

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._heartbeat, name, tg.cancel_scope)
                try:
                    yield True
                finally:
                    tg.cancel_scope.cancel()
        finally:
            self._release(name)

    async def _heartbeat(self, name: str, cancel_scope: anyio.CancelScope) -> None:
        while not cancel_scope.cancel_called:
            await anyio.sleep(self._config.renew_interval_seconds)

            entry = self._store.get(name)
            if entry is None or entry[0] != self._holder_id:
                cancel_scope.cancel()
                return

            self._store[name] = (self._holder_id, self._now() + timedelta(seconds=self._config.ttl_seconds))

    def _release(self, name: str) -> None:
        entry = self._store.get(name)
        if entry is not None and entry[0] == self._holder_id:
            del self._store[name]
