from __future__ import annotations

import contextlib
import logging

# Dishka introspects the session factory signatures, so these annotations must resolve at runtime.
from collections.abc import (
    AsyncGenerator,  # noqa: TC003
    AsyncIterator,  # noqa: TC003
    Callable,  # noqa: TC003
)
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.lease import ILease, InMemoryLease, LeaseConfig
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.di import object_
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import LeadershipConfig, MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging._internal.maintenance import DurabilityMaintenanceAgent, LeadershipCoordinator
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox import EndpointUri, HandlerDestination
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.sequence import ISequenceAllocator
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from dishka import Provider


_LEADER_KEY = 'waku:leader'


class _CountingInboxStore(FakeInboxStore):
    def __init__(self) -> None:
        super().__init__()
        self.promote_calls = 0

    @override
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        self.promote_calls += 1
        return await super().promote_due_scheduled(now, allocator, batch_size)


class _RaisingLease(ILease):
    def __init__(self) -> None:
        self.attempts = 0

    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        self.attempts += 1
        msg = 'lease backend down'
        raise ConnectionError(msg)
        yield False  # type: ignore[unreachable]  # pragma: no cover -- async-generator shape only, never reached


class _HangingReleaseLease(ILease):
    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        try:
            yield True
        finally:
            await anyio.Event().wait()  # hang on release to force the shutdown-timeout cancel path


def _session_factory_over(engine: AsyncEngine) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def factory() -> AsyncIterator[AsyncSession]:  # pragma: no cover -- never resolved before the fail-loud check
        session = AsyncSession(engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    return factory


def _lease_providers(lease: ILease, *extra: Provider) -> list[Provider]:
    return [object_(lease, provided_type=ILease), *extra]


class TestFailLoud:
    @staticmethod
    async def test_leadership_without_lease_provider_fails_loud() -> None:
        # sqla backend registered WITHOUT engine= ⇒ no ILease provider; leadership configured ⇒ the
        # coordinator fails loud at after_app_init, naming the engine remedy.
        engine = create_async_engine('postgresql+psycopg://localhost/waku_leadership_test')
        config = MessagingConfig(outbox=OutboxConfig(), leadership=LeadershipConfig())
        try:
            with pytest.raises(ImproperlyConfiguredError, match='engine'):
                async with create_test_app(
                    imports=[
                        MessagingModule.register(config),
                        SqlAlchemyBackend.register(session_factory=_session_factory_over(engine)),
                    ],
                ):
                    pass  # pragma: no cover -- after_app_init raises before the body runs
        finally:
            await engine.dispose()


class TestLeaderRunsAgent:
    @staticmethod
    async def test_leader_acquires_lease_and_runs_the_maintenance_agent() -> None:
        lease = InMemoryLease(LeaseConfig(ttl_seconds=0.2), store={}, now=utc_now)
        inbox = _CountingInboxStore()
        config = MessagingConfig(
            inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)),
            leadership=LeadershipConfig(lease=LeaseConfig(ttl_seconds=0.2)),
        )
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(
                lease,
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                object_(RecordingUoW(), provided_type=IUnitOfWork),
            ),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            try:
                await wait_until(lambda: coordinator.is_leader and inbox.promote_calls >= 1)
            finally:
                await coordinator.on_app_shutdown(app)

        assert coordinator.is_leader is False


class TestHandover:
    @staticmethod
    async def test_steal_stops_the_agent_under_bounded_real_time() -> None:
        # §9 handover: a steal is detected ONLY by node-1's own heartbeat firing (real sub-second cadence
        # under a short ttl) and observing a different holder — clock-advance alone can't wake it. Poll for
        # the state transition under wait_until's fail_after bound, never assert it immediately.
        store: dict[str, tuple[str, datetime]] = {}
        lease = InMemoryLease(LeaseConfig(ttl_seconds=0.2), store=store, now=utc_now)
        config = MessagingConfig(leadership=LeadershipConfig(lease=LeaseConfig(ttl_seconds=0.2)))
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            try:
                await wait_until(lambda: coordinator.is_leader)
                # A second node steals the lease: overwrite the row with a different, live holder.
                store[_LEADER_KEY] = ('node-2', utc_now() + timedelta(seconds=60))
                await wait_until(lambda: not coordinator.is_leader)
            finally:
                await coordinator.on_app_shutdown(app)

        assert coordinator.is_leader is False
        assert store[_LEADER_KEY][0] == 'node-2'  # node-1 never clobbered the stealer's lease


class TestGracefulShutdown:
    @staticmethod
    async def test_graceful_shutdown_releases_the_lease() -> None:
        store: dict[str, tuple[str, datetime]] = {}
        lease = InMemoryLease(LeaseConfig(ttl_seconds=5.0), store=store, now=utc_now)
        config = MessagingConfig(leadership=LeadershipConfig(lease=LeaseConfig(ttl_seconds=5.0)))
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            await wait_until(lambda: coordinator.is_leader)
            assert _LEADER_KEY in store

            await coordinator.on_app_shutdown(app)

            assert coordinator.is_leader is False
            assert _LEADER_KEY not in store  # released for immediate standby handover, no TTL wait


class TestCoordinatorResilience:
    @staticmethod
    async def test_acquire_loop_retries_after_a_lease_error() -> None:
        # A transient lease failure logs and retries — it never crashes the loop or claims leadership.
        lease = _RaisingLease()
        config = MessagingConfig(leadership=LeadershipConfig(lease=LeaseConfig(ttl_seconds=0.2)))
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            try:
                await wait_until(lambda: lease.attempts >= 2)  # retried past the first raised error
            finally:
                await coordinator.on_app_shutdown(app)

        assert coordinator.is_leader is False

    @staticmethod
    async def test_shutdown_cancels_a_coordinator_that_will_not_release(caplog: pytest.LogCaptureFixture) -> None:
        # The lease release hangs on shutdown; the coordinator cancels the loop after stop_timeout.
        config = MessagingConfig(
            leadership=LeadershipConfig(lease=LeaseConfig(ttl_seconds=5.0), stop_timeout=timedelta(seconds=0.05)),
        )
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(_HangingReleaseLease()),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            await wait_until(lambda: coordinator.is_leader)
            with caplog.at_level(logging.WARNING, logger='waku.messaging._internal.maintenance'):
                await coordinator.on_app_shutdown(app)

        assert 'did not terminate' in caplog.text


class TestNoLeaderPathUnchanged:
    @staticmethod
    def test_leadership_off_wires_unconditional_maintenance_owner() -> None:
        dynamic = MessagingModule.register(MessagingConfig(outbox=OutboxConfig()))
        types = {type(ext).__name__ for ext in dynamic.extensions}
        assert 'DurabilityMaintenanceLifecycleExtension' in types
        assert 'LeadershipCoordinator' not in types

    @staticmethod
    def test_leadership_on_wires_the_coordinator_instead() -> None:
        dynamic = MessagingModule.register(MessagingConfig(outbox=OutboxConfig(), leadership=LeadershipConfig()))
        types = {type(ext).__name__ for ext in dynamic.extensions}
        assert 'LeadershipCoordinator' in types
        assert 'DurabilityMaintenanceLifecycleExtension' not in types


class TestOverlapIsSafe:
    @staticmethod
    async def test_two_overlapping_agents_promote_each_row_once() -> None:
        # Justifies the plain (non-mutual-exclusion) lease: during a brief steal two nodes may run the
        # agent at once. Two agents over ONE inbox store must not double-promote — each SCHEDULED row
        # flips to INCOMING exactly once (idempotency, D1).
        inbox = FakeInboxStore()
        due = [
            InboxEntry(
                id=uuid4(),
                payload={'n': i},
                message_type='test.Event',
                source_uri=EndpointUri('local://orders'),
                destination=HandlerDestination('tests.messaging.HandlerA'),
                correlation_id=str(uuid4()),
                causation_id=str(uuid4()),
                status=InboxStatus.SCHEDULED,
                execution_time=datetime.now(tz=UTC) - timedelta(minutes=1),
                owner_id=None,
                group_id=None,
            )
            for i in range(5)
        ]
        for entry in due:
            await inbox.store_incoming(entry)

        config = MessagingConfig(inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)))
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=[
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                object_(RecordingUoW(), provided_type=IUnitOfWork),
            ],
        ) as app:
            agent_a = DurabilityMaintenanceAgent(container=app.container, config=config)
            agent_b = DurabilityMaintenanceAgent(container=app.container, config=config)
            await agent_a.start()
            await agent_b.start()
            try:
                await wait_until(
                    lambda: sum(e.status is InboxStatus.INCOMING for e in inbox.entries.values()) == len(due),
                )
            finally:
                await agent_a.stop()
                await agent_b.stop()

        incoming = [e for e in inbox.entries.values() if e.status is InboxStatus.INCOMING]
        assert len(incoming) == len(due)  # no phantom/duplicated promotions from the overlap
