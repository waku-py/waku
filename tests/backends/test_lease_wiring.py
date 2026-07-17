from __future__ import annotations

from collections.abc import AsyncIterator, Callable  # noqa: TC003 -- dishka introspects the session factory signature
from typing import TYPE_CHECKING

import pytest
from dishka.exceptions import NoActiveFactoryError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from waku._internal.lease import ILease, InMemoryLease
from waku.backends.memory import MemoryBackend
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.backends.sqlalchemy.lease.store import PostgresLease
from waku.di import is_registered
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.messaging import LeadershipConfig, MessagingConfig, MessagingModule
from waku.testing import create_test_app

if TYPE_CHECKING:
    from waku.modules._internal.metadata import DynamicModule


@pytest.fixture
async def lease_engine() -> AsyncIterator[AsyncEngine]:
    # A non-connected engine: these tests exercise WIRING/gating only — the lease's SQL never runs, so
    # no live PostgreSQL is needed (PostgresLease.__init__ just holds the engine).
    engine = create_async_engine('postgresql+psycopg://localhost/waku_lease_wiring_test')
    try:
        yield engine
    finally:
        await engine.dispose()


def _session_factory_over(engine: AsyncEngine) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def factory() -> AsyncIterator[AsyncSession]:  # pragma: no cover -- never resolved (registration-only tests)
        session = AsyncSession(engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    return factory


def _sqla_backend(engine: AsyncEngine, *, with_engine: bool) -> DynamicModule:
    factory = _session_factory_over(engine)
    if with_engine:
        return SqlAlchemyBackend.register(session_factory=factory, engine=engine)
    return SqlAlchemyBackend.register(session_factory=factory)


class TestLeadershipOffIsGraphIdentical:
    # The MANDATORY D5 regression guard: leadership=None + no engine= registers ZERO lease providers.

    @staticmethod
    async def test_sqla_leadership_off_no_engine_registers_no_lease(lease_engine: AsyncEngine) -> None:
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                _sqla_backend(lease_engine, with_engine=False),
            ],
        ) as app:
            assert await is_registered(app.container, ILease) is False
            assert await is_registered(app.container, AsyncEngine) is False

    @staticmethod
    async def test_memory_leadership_off_leaves_lease_inactive() -> None:
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                MemoryBackend.register(),
            ],
        ) as app:
            # Memory registers an INERT provider (the type-presence seam can't read the .leadership
            # value), so the activator keeps it deactivated — behaviorally identical to today.
            assert await is_registered(app.container, ILease) is False


class TestLeadershipOnResolvesLease:
    @staticmethod
    async def test_sqla_resolves_postgres_lease(lease_engine: AsyncEngine) -> None:
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig(leadership=LeadershipConfig())),
                _sqla_backend(lease_engine, with_engine=True),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, PostgresLease)

    @staticmethod
    async def test_memory_resolves_in_memory_lease() -> None:
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig(leadership=LeadershipConfig())),
                MemoryBackend.register(),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, InMemoryLease)


class TestActivatorGatesSelection:
    @staticmethod
    async def test_sqla_engine_present_leadership_off_lease_inactive(lease_engine: AsyncEngine) -> None:
        # The activator earns its keep: with engine= passed but leadership off, ILease is registered but
        # DEACTIVATED — resolving it raises rather than constructing a lease nobody should own.
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                _sqla_backend(lease_engine, with_engine=True),
            ],
        ) as app:
            assert await is_registered(app.container, ILease) is False
            with pytest.raises(NoActiveFactoryError):
                await app.container.get(ILease)


class TestProjectionLeaseIsBackendOwned:
    # Option 2: when event sourcing is present the backend registers the projection-daemon lease
    # UNGATED — resolvable regardless of messaging leadership, and even with no messaging at all.

    @staticmethod
    async def test_es_only_memory_app_resolves_in_memory_lease() -> None:
        async with create_test_app(
            imports=[
                EventSourcingModule.register(EventSourcingConfig()),
                MemoryBackend.register(),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, InMemoryLease)

    @staticmethod
    async def test_es_only_sqla_app_resolves_postgres_lease(lease_engine: AsyncEngine) -> None:
        async with create_test_app(
            imports=[
                EventSourcingModule.register(EventSourcingConfig()),
                _sqla_backend(lease_engine, with_engine=True),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, PostgresLease)

    @staticmethod
    async def test_memory_es_with_leadership_off_still_resolves() -> None:
        # Messaging present but leadership OFF, ES present ⇒ the projection lease is resolvable even
        # though the messaging leadership path never uses it (the leadership-off default-config branch).
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                EventSourcingModule.register(EventSourcingConfig()),
                MemoryBackend.register(),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, InMemoryLease)

    @staticmethod
    async def test_sqla_es_with_leadership_off_still_resolves(lease_engine: AsyncEngine) -> None:
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                EventSourcingModule.register(EventSourcingConfig()),
                _sqla_backend(lease_engine, with_engine=True),
            ],
        ) as app:
            lease = await app.container.get(ILease)
            assert isinstance(lease, PostgresLease)
