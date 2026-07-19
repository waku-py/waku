from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import anyio
import pytest

from waku._internal.lease import LeaseConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku._internal.lease import ILease

__all__ = ['LeaseBackend', 'LeaseContract']


@dataclass(frozen=True, slots=True)
class LeaseBackend:
    make: Callable[[LeaseConfig], ILease]
    """Build a distinct holder over the ONE shared resource; the config picks the TTL each test needs."""
    expire: Callable[[str], Awaitable[None]]
    """Force the named lease expired in place. Only invoked when ``supports_expiry`` is True."""


class LeaseContract:
    """Behavioral contract every :class:`~waku._internal.lease.ILease` implementation must pass.

    Subclass in your backend's test suite and override the ``lease_backend`` fixture with a
    :class:`LeaseBackend` over a fresh resource per test. Backends without a TTL/expiry primitive
    (a session-bound advisory lock cannot be time-expired) set ``supports_expiry = False`` to opt out
    of the expiry-reassert behaviors.
    """

    supports_expiry: ClassVar[bool] = True

    @pytest.fixture
    def lease_backend(self) -> LeaseBackend:
        msg = 'override the lease_backend fixture with your backend provider'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_acquire_grants(self, lease_backend: LeaseBackend) -> None:
        async with lease_backend.make(LeaseConfig()).acquire('orders') as acquired:
            assert acquired is True

    async def test_sibling_blocked_while_held(self, lease_backend: LeaseBackend) -> None:
        async with lease_backend.make(LeaseConfig()).acquire('orders') as first:
            assert first is True
            async with lease_backend.make(LeaseConfig()).acquire('orders') as second:
                assert second is False

    async def test_release_allows_reacquire(self, lease_backend: LeaseBackend) -> None:
        holder = lease_backend.make(LeaseConfig())
        async with holder.acquire('orders') as acquired:
            assert acquired is True

        async with holder.acquire('orders') as reacquired:
            assert reacquired is True

    async def test_expired_is_reacquired_by_sibling(self, lease_backend: LeaseBackend) -> None:
        if not self.supports_expiry:
            pytest.skip('backend has no TTL-based expiry')

        async with lease_backend.make(LeaseConfig()).acquire('orders') as first:
            assert first is True
            await lease_backend.expire('orders')
            async with lease_backend.make(LeaseConfig()).acquire('orders') as second:
                assert second is True

    async def test_expired_holder_heartbeat_does_not_resurrect(self, lease_backend: LeaseBackend) -> None:
        if not self.supports_expiry:
            pytest.skip('backend has no TTL-based expiry')

        holder = lease_backend.make(LeaseConfig(ttl_seconds=0.5))
        acquired = anyio.Event()
        body_exited = anyio.Event()

        async def hold() -> None:
            async with holder.acquire('orders') as got:
                assert got is True
                acquired.set()
                await anyio.sleep_forever()
            body_exited.set()

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(hold)
                await acquired.wait()
                await lease_backend.expire('orders')
                await body_exited.wait()

    async def test_release_survives_parent_cancellation(self, lease_backend: LeaseBackend) -> None:
        holder = lease_backend.make(LeaseConfig())
        standby = lease_backend.make(LeaseConfig())

        with anyio.CancelScope() as scope:
            async with holder.acquire('orders') as held:
                assert held is True
                scope.cancel()

        async with standby.acquire('orders') as took:
            assert took is True
