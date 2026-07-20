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
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.lease import ILease, InMemoryLease, LeaseConfig
from waku.di import object_
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import LeadershipConfig, MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging._internal.maintenance import DurabilityMaintenanceAgent, LeadershipCoordinator
from waku.messaging._internal.polling_agent import FixedPace, Placement, PollingAgent
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox import EndpointUri, HandlerDestination
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.sequence import ISequenceAllocator
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingUoW, durability_providers
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from dishka import Provider
    from pytest_mock import MockerFixture


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


def _lease_providers(lease: ILease, lease_config: LeaseConfig, *extra: Provider) -> list[Provider]:
    # The backend publishes both ILease and its LeaseConfig; the coordinator reads the config for its
    # standby re-acquire cadence, so every leadership app registers both.
    return [
        object_(lease, provided_type=ILease),
        object_(lease_config, provided_type=LeaseConfig),
        *extra,
    ]


class TestFailLoud:
    @staticmethod
    async def test_leadership_without_lease_provider_fails_loud() -> None:
        # A backend publishing durability stores but no ILease, with leadership configured ⇒ assembling
        # the app fails loud at after_app_init, naming the engine remedy. In-memory durability keeps the
        # node's own membership registration off any database while still exercising real assembly.
        config = MessagingConfig(outbox=OutboxConfig(), leadership=LeadershipConfig())
        with pytest.raises(ImproperlyConfiguredError, match='engine'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=durability_providers(),
            ):
                pass  # pragma: no cover -- assembly raises before the body runs

    @staticmethod
    async def test_leadership_with_lease_but_no_lease_config_fails_loud() -> None:
        # A custom backend publishing ILease but omitting its LeaseConfig ⇒ the coordinator fails loud at
        # after_app_init naming LeaseConfig, mirroring the ILease guard rather than a raw dishka error.
        lease = InMemoryLease(LeaseConfig(), store={}, now=utc_now)
        config = MessagingConfig(outbox=OutboxConfig(), leadership=LeadershipConfig())
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=[object_(lease, provided_type=ILease)],
        ) as app:
            coordinator = LeadershipCoordinator(config)
            with pytest.raises(ImproperlyConfiguredError, match='LeaseConfig'):
                await coordinator.after_app_init(app)


class TestLeaderRunsAgent:
    @staticmethod
    async def test_leader_acquires_lease_and_runs_the_maintenance_agent() -> None:
        lease_config = LeaseConfig(ttl_seconds=0.2)
        lease = InMemoryLease(lease_config, store={}, now=utc_now)
        inbox = _CountingInboxStore()
        config = MessagingConfig(
            inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)),
            leadership=LeadershipConfig(),
        )
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(
                lease,
                lease_config,
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
        lease_config = LeaseConfig(ttl_seconds=0.2)
        lease = InMemoryLease(lease_config, store=store, now=utc_now)
        config = MessagingConfig(leadership=LeadershipConfig())
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease, lease_config),
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
        lease_config = LeaseConfig(ttl_seconds=5.0)
        lease = InMemoryLease(lease_config, store=store, now=utc_now)
        config = MessagingConfig(leadership=LeadershipConfig())
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease, lease_config),
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
        lease_config = LeaseConfig(ttl_seconds=0.2)
        config = MessagingConfig(leadership=LeadershipConfig())
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease, lease_config),
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
        lease_config = LeaseConfig(ttl_seconds=5.0)
        config = MessagingConfig(
            leadership=LeadershipConfig(stop_timeout=timedelta(seconds=0.05)),
        )
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(_HangingReleaseLease(), lease_config),
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


class _RecordingPoller(PollingAgent):
    """Idle poller that counts its start/stop calls so a leaked started poller is observable."""

    placement = Placement.PER_POD

    def __init__(self) -> None:
        super().__init__(stop_timeout=timedelta(seconds=1))
        self.start_count = 0
        self.stop_count = 0

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(60.0)

    @override
    async def _tick(self) -> int:
        return 0

    @override
    async def start(self) -> None:
        self.start_count += 1
        await super().start()

    @override
    async def stop(self) -> None:
        self.stop_count += 1
        await super().stop()


class _StartRaisingPoller(PollingAgent):
    """Poller whose start() always raises, modelling a later poller failing mid-agent-startup."""

    placement = Placement.PER_POD

    def __init__(self) -> None:
        super().__init__(stop_timeout=timedelta(seconds=1))

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(60.0)

    @override
    async def _tick(self) -> int:
        return 0

    @override
    async def start(self) -> None:
        msg = 'poller start failed'
        raise RuntimeError(msg)


class _InjectedPollerAgent(DurabilityMaintenanceAgent):
    """Agent with test-supplied pollers, reusing the real reversed, error-aggregating start/stop."""

    def __init__(self, pollers: tuple[PollingAgent, ...]) -> None:
        self._pollers = pollers


class TestPartialStartCleanup:
    @staticmethod
    async def test_start_failure_stops_the_already_started_poller(mocker: MockerFixture) -> None:
        # The agent starts pollers sequentially; when the second poller's start() raises, the coordinator
        # must still stop the first (already started) poller — the try/finally, not the post-block cleanup.
        recording = _RecordingPoller()
        agent = _InjectedPollerAgent((recording, _StartRaisingPoller()))
        mocker.patch(
            'waku.messaging._internal.maintenance._build_maintenance_agent',
            new_callable=mocker.AsyncMock,
            return_value=agent,
        )
        lease_config = LeaseConfig(ttl_seconds=0.2)
        lease = InMemoryLease(lease_config, store={}, now=utc_now)
        config = MessagingConfig(leadership=LeadershipConfig())
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=_lease_providers(lease, lease_config),
        ) as app:
            coordinator = LeadershipCoordinator(config)
            await coordinator.after_app_init(app)
            try:
                await wait_until(lambda: recording.stop_count >= 1)
            finally:
                await coordinator.on_app_shutdown(app)

        assert recording.start_count >= 1
        assert recording.stop_count >= 1  # the started poller was stopped despite the sibling's failure
