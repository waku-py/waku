from __future__ import annotations

import abc
import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, TypeAlias

import anyio
from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

__all__ = [
    'DEFAULT_LEASE_CONFIG',
    'HeartbeatLease',
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


DEFAULT_LEASE_CONFIG: Final = LeaseConfig()


class HeartbeatLease(ILease):
    """Heartbeat-renewed single-owner lease: claim, renew on a timer until lost or released, then release.

    The claim/heartbeat/renew/release choreography lives here once; a backend implements only the three
    storage hooks. A renew that reports the lease is no longer held cancels the held body, so the owner
    observes the loss as cancellation at the ``acquire`` context-manager boundary.
    """

    def __init__(self, config: LeaseConfig) -> None:
        self._config = config
        self._holder_id = str(uuid.uuid4())

    @contextlib.asynccontextmanager
    @override
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        if not await self._try_claim(name):
            yield False
            return

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._heartbeat, name, tg.cancel_scope)
                try:
                    yield True
                finally:
                    tg.cancel_scope.cancel()
        finally:
            await self._release(name)

    async def _heartbeat(self, name: str, cancel_scope: anyio.CancelScope) -> None:
        while not cancel_scope.cancel_called:
            await anyio.sleep(self._config.renew_interval_seconds)
            if not await self._renew(name):
                cancel_scope.cancel()
                return

    @abc.abstractmethod
    async def _try_claim(self, name: str) -> bool:
        """Claim the lease for ``name``; return True iff this holder now owns it (False if held elsewhere)."""

    @abc.abstractmethod
    async def _renew(self, name: str) -> bool:
        """Refresh this holder's claim; return False if the lease is no longer held (expired or stolen)."""

    @abc.abstractmethod
    async def _release(self, name: str) -> None:
        """Release this holder's claim for ``name`` if still held; must not raise if the claim was lost."""


class InMemoryLease(HeartbeatLease):
    """In-process lease with injected-clock expiry, mirroring the PostgreSQL lease shape.

    Default construction gets a private store (single-node isolation). Passing a shared ``store``
    with distinct per-instance ``holder_id`` models contending nodes over one backing table.
    """

    def __init__(
        self,
        config: LeaseConfig = DEFAULT_LEASE_CONFIG,
        *,
        store: dict[str, _Entry] | None = None,
        now: Now = utc_now,
    ) -> None:
        super().__init__(config)
        self._store = store if store is not None else {}
        self._now = now

    @override
    async def _try_claim(self, name: str) -> bool:
        entry = self._store.get(name)
        if entry is not None and entry[1] > self._now():
            return False
        self._store[name] = (self._holder_id, self._now() + timedelta(seconds=self._config.ttl_seconds))
        return True

    @override
    async def _renew(self, name: str) -> bool:
        entry = self._store.get(name)
        if entry is None or entry[0] != self._holder_id:
            return False
        self._store[name] = (self._holder_id, self._now() + timedelta(seconds=self._config.ttl_seconds))
        return True

    @override
    async def _release(self, name: str) -> None:
        entry = self._store.get(name)
        if entry is not None and entry[0] == self._holder_id:
            del self._store[name]
