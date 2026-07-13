from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging import PollingConfig
from waku.messaging._internal.maintenance import (
    DurabilityMaintenanceAgent,
    _DlqMaintenancePoller,
    _OutboxMaintenancePoller,
    _PromotionPoller,
)
from waku.messaging.config import DeadLetterConfig, MessagingConfig, OutboxConfig
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.outbox.relay import OutboxRelayConfig
from waku.messaging.partition import ISequenceAllocator
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingAllocator, RecordingDeadLetterStore
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import FakeOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    import pytest

_FAST = PollingConfig(poll_interval_min_seconds=0.01, poll_interval_max_seconds=0.05, poll_interval_step_seconds=0.01)


class _MaintOutboxStore(FakeOutboxStore):
    def __init__(
        self,
        *,
        recover_count: int = 0,
        cleanup_count: int = 0,
        recover_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.recover_calls = 0
        self.cleanup_calls = 0
        self._recover_count = recover_count
        self._cleanup_count = cleanup_count
        self._recover_error = recover_error

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        self.recover_calls += 1
        if self._recover_error is not None:
            err, self._recover_error = self._recover_error, None
            raise err
        return self._recover_count

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        self.cleanup_calls += 1
        return self._cleanup_count


class _MaintDlqStore(RecordingDeadLetterStore):
    def __init__(self, *, claimable: Sequence[DeadLetterEntry] = (), purge_count: int = 0) -> None:
        super().__init__()
        self.claim_calls = 0
        self.purged: list[datetime] = []
        self._to_claim = list(claimable)
        self._purge_count = purge_count

    @override
    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        self.claim_calls += 1
        batch, self._to_claim = self._to_claim[:batch_size], self._to_claim[batch_size:]
        return batch

    @override
    async def purge(self, older_than: datetime) -> int:
        self.purged.append(older_than)
        return self._purge_count


class _MaintInboxStore(FakeInboxStore):
    def __init__(self, *, promote_count: int = 0) -> None:
        super().__init__()
        self.promote_calls = 0
        self._promote_count = promote_count

    @override
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        self.promote_calls += 1
        return self._promote_count


class _RecordingReplayExecutor(ReplayExecutor):
    __slots__ = ('replayed',)

    def __init__(self) -> None:
        self.replayed: list[UUID] = []

    @override
    async def replay(self, entry: DeadLetterEntry) -> bool:
        self.replayed.append(entry.id)
        return True


class _BlockingReplayExecutor(ReplayExecutor):
    __slots__ = ('_entered', '_released')

    def __init__(self, entered: anyio.Event, released: anyio.Event) -> None:
        self._entered = entered
        self._released = released

    @override
    async def replay(self, entry: DeadLetterEntry) -> bool:
        self._entered.set()
        await self._released.wait()
        return True


class _MaintenanceDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        *,
        outbox: IOutboxStore,
        dlq: IDeadLetterStore,
        inbox: IInboxStore,
        replayer: ReplayExecutor,
    ) -> None:
        super().__init__()
        self._outbox = outbox
        self._dlq = dlq
        self._inbox = inbox
        self._replayer = replayer
        self._allocator = RecordingAllocator()
        self._uow: IUnitOfWork = FakeUoW()

    @provide
    def outbox(self) -> IOutboxStore:
        return self._outbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def allocator(self) -> ISequenceAllocator:
        return self._allocator

    @provide
    def replayer(self) -> ReplayExecutor:
        return self._replayer

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


def _dlq_entry() -> DeadLetterEntry:
    return DeadLetterEntry(
        id=uuid4(),
        message_type='test.Event',
        payload={'test': True},
        destination='local://dlq',
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id='c',
        causation_id='c2',
        error_type='RuntimeError',
        error_message='boom',
        retry_count=1,
    )


def _deps(
    *,
    outbox: IOutboxStore | None = None,
    dlq: IDeadLetterStore | None = None,
    inbox: IInboxStore | None = None,
    replayer: ReplayExecutor | None = None,
) -> _MaintenanceDepsProvider:
    return _MaintenanceDepsProvider(
        outbox=outbox or _MaintOutboxStore(),
        dlq=dlq or _MaintDlqStore(),
        inbox=inbox or _MaintInboxStore(),
        replayer=replayer or _RecordingReplayExecutor(),
    )


def _all_three_config() -> MessagingConfig:
    return MessagingConfig(
        outbox=OutboxConfig(
            relay=OutboxRelayConfig(polling=_FAST, recovery_interval=timedelta(seconds=0)),
        ),
        dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST),
        inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)),
    )


class TestConfiguredSubsetOnly:
    @staticmethod
    async def test_outbox_only_starts_one_outbox_poller() -> None:
        async with make_async_container() as container:
            agent = DurabilityMaintenanceAgent(container=container, config=MessagingConfig(outbox=OutboxConfig()))
        assert [type(p) for p in agent.pollers] == [_OutboxMaintenancePoller]

    @staticmethod
    async def test_dlq_only_starts_one_dlq_poller() -> None:
        async with make_async_container() as container:
            agent = DurabilityMaintenanceAgent(
                container=container,
                config=MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True)),
            )
        assert [type(p) for p in agent.pollers] == [_DlqMaintenancePoller]

    @staticmethod
    async def test_dlq_without_replay_or_retention_starts_no_poller() -> None:
        async with make_async_container() as container:
            agent = DurabilityMaintenanceAgent(
                container=container,
                config=MessagingConfig(dead_letter=DeadLetterConfig()),
            )
        assert agent.pollers == ()

    @staticmethod
    async def test_inbox_only_starts_one_promotion_poller() -> None:
        async with make_async_container() as container:
            agent = DurabilityMaintenanceAgent(container=container, config=MessagingConfig(inbox=InboxConfig()))
        assert [type(p) for p in agent.pollers] == [_PromotionPoller]

    @staticmethod
    async def test_all_three_start_three_pollers() -> None:
        async with make_async_container() as container:
            agent = DurabilityMaintenanceAgent(container=container, config=_all_three_config())
        assert [type(p) for p in agent.pollers] == [
            _OutboxMaintenancePoller,
            _DlqMaintenancePoller,
            _PromotionPoller,
        ]


class TestEachConcernRunsItsOwnOperation:
    @staticmethod
    async def test_every_configured_concern_fires_its_store_operation() -> None:
        outbox = _MaintOutboxStore(recover_count=1)
        dlq = _MaintDlqStore(claimable=[_dlq_entry()])
        inbox = _MaintInboxStore(promote_count=1)
        replayer = _RecordingReplayExecutor()
        provider = _MaintenanceDepsProvider(outbox=outbox, dlq=dlq, inbox=inbox, replayer=replayer)

        async with make_async_container(provider) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=_all_three_config())
            await agent.start()
            try:
                await wait_until(
                    lambda: outbox.recover_calls >= 1 and bool(replayer.replayed) and inbox.promote_calls >= 1,
                )
            finally:
                await agent.stop()

        assert outbox.recover_calls >= 1
        assert dlq.claim_calls >= 1
        assert replayer.replayed
        assert inbox.promote_calls >= 1


class TestNoHeadOfLineBlocking:
    @staticmethod
    async def test_blocked_dlq_replay_does_not_stall_outbox_or_promotion() -> None:
        # The regression guard for the independent-child-task shape: a DLQ replay parked forever must
        # NOT delay the outbox recovery-sweep or the scheduled-promotion poller — they run as separate
        # asyncio tasks, each on its own cadence.
        entered = anyio.Event()
        released = anyio.Event()
        outbox = _MaintOutboxStore(recover_count=1)
        dlq = _MaintDlqStore(claimable=[_dlq_entry()])
        inbox = _MaintInboxStore(promote_count=1)
        replayer = _BlockingReplayExecutor(entered, released)
        provider = _MaintenanceDepsProvider(outbox=outbox, dlq=dlq, inbox=inbox, replayer=replayer)

        async with make_async_container(provider) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=_all_three_config())
            await agent.start()
            try:
                await wait_until(entered.is_set)  # DLQ poller now parked inside replay
                await wait_until(lambda: outbox.recover_calls >= 2 and inbox.promote_calls >= 2)
            finally:
                released.set()
                await agent.stop()

        assert outbox.recover_calls >= 2
        assert inbox.promote_calls >= 2
        assert dlq.claim_calls == 1  # claimed once, then parked in replay — never returned to re-claim


class TestOutboxMaintenancePoller:
    # Ported from tests/messaging/outbox/test_relay.py (recover/cleanup moved off the relay).

    @staticmethod
    async def test_recovers_stuck_messages(caplog: pytest.LogCaptureFixture) -> None:
        outbox = _MaintOutboxStore(recover_count=5)
        config = MessagingConfig(
            outbox=OutboxConfig(relay=OutboxRelayConfig(polling=_FAST, recovery_interval=timedelta(seconds=0))),
        )
        with caplog.at_level(logging.INFO, logger='waku.messaging._internal.maintenance'):
            async with make_async_container(_deps(outbox=outbox)) as container:
                agent = DurabilityMaintenanceAgent(container=container, config=config)
                await agent.start()
                try:
                    await wait_until(lambda: 'Recovered 5 stuck messages' in caplog.text)
                finally:
                    await agent.stop()

        assert 'Recovered 5 stuck messages' in caplog.text

    @staticmethod
    async def test_purges_dispatched_when_retention_elapsed(caplog: pytest.LogCaptureFixture) -> None:
        outbox = _MaintOutboxStore(cleanup_count=3)
        config = MessagingConfig(
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=_FAST,
                    recovery_interval=timedelta(seconds=0),
                    retention=timedelta(hours=1),
                    cleanup_interval=timedelta(seconds=0),
                ),
            ),
        )
        with caplog.at_level(logging.INFO, logger='waku.messaging._internal.maintenance'):
            async with make_async_container(_deps(outbox=outbox)) as container:
                agent = DurabilityMaintenanceAgent(container=container, config=config)
                await agent.start()
                try:
                    await wait_until(
                        lambda: 'Purged 3 dispatched outbox messages older than retention' in caplog.text,
                    )
                finally:
                    await agent.stop()

        assert 'Purged 3 dispatched outbox messages older than retention' in caplog.text

    @staticmethod
    async def test_does_not_purge_when_retention_unset() -> None:
        outbox = _MaintOutboxStore(recover_count=1)
        config = MessagingConfig(
            outbox=OutboxConfig(relay=OutboxRelayConfig(polling=_FAST, recovery_interval=timedelta(seconds=0))),
        )
        async with make_async_container(_deps(outbox=outbox)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: outbox.recover_calls >= 1)
            finally:
                await agent.stop()

        assert outbox.cleanup_calls == 0

    @staticmethod
    async def test_recovery_failure_does_not_crash_loop() -> None:
        outbox = _MaintOutboxStore(recover_error=ConnectionError('recovery backend down'))
        config = MessagingConfig(
            outbox=OutboxConfig(relay=OutboxRelayConfig(polling=_FAST, recovery_interval=timedelta(seconds=0))),
        )
        async with make_async_container(_deps(outbox=outbox)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: outbox.recover_calls >= 2)  # loop survived the raised error
            finally:
                await agent.stop()

        assert outbox.recover_calls >= 2

    @staticmethod
    async def test_recover_and_cleanup_fire_once_per_interval() -> None:
        # The interval gate: with a large recovery/cleanup interval the outbox poller ticks many times
        # (promotion climbing proves elapsed cycles) but each op fires exactly once, not every tick.
        outbox = _MaintOutboxStore(recover_count=1, cleanup_count=1)
        inbox = _MaintInboxStore(promote_count=1)
        config = MessagingConfig(
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=_FAST,
                    recovery_interval=timedelta(hours=1),
                    retention=timedelta(hours=1),
                    cleanup_interval=timedelta(hours=1),
                ),
            ),
            inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)),
        )
        async with make_async_container(_deps(outbox=outbox, inbox=inbox)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: inbox.promote_calls >= 3)  # several cycles elapsed
            finally:
                await agent.stop()

        assert outbox.recover_calls == 1
        assert outbox.cleanup_calls == 1


class TestDlqMaintenancePoller:
    # Ported from tests/messaging/errors/test_worker.py (DeadLetterWorker subsumed).

    @staticmethod
    async def test_replays_claimed_entries() -> None:
        entry = _dlq_entry()
        replayer = _RecordingReplayExecutor()
        config = MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST))
        async with make_async_container(_deps(dlq=_MaintDlqStore(claimable=[entry]), replayer=replayer)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: bool(replayer.replayed))
            finally:
                await agent.stop()

        assert replayer.replayed == [entry.id]

    @staticmethod
    async def test_does_not_claim_when_auto_replay_disabled() -> None:
        dlq = _MaintDlqStore(claimable=[_dlq_entry()], purge_count=1)
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(
                auto_replay_enabled=False,
                retention=timedelta(days=30),
                cleanup_interval=timedelta(seconds=0),
                polling=_FAST,
            ),
        )
        async with make_async_container(_deps(dlq=dlq)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: bool(dlq.purged))  # observable tick: purge ran
            finally:
                await agent.stop()

        assert dlq.claim_calls == 0

    @staticmethod
    async def test_purges_when_retention_set() -> None:
        dlq = _MaintDlqStore(purge_count=2)
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(
                auto_replay_enabled=False,
                retention=timedelta(days=30),
                cleanup_interval=timedelta(seconds=0),
                polling=_FAST,
            ),
        )
        async with make_async_container(_deps(dlq=dlq)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: bool(dlq.purged))
            finally:
                await agent.stop()

        assert len(dlq.purged) >= 1

    @staticmethod
    async def test_purge_fires_once_per_interval_while_replay_runs_every_tick() -> None:
        # Purge is gated by cleanup_interval (fires once) while auto-replay claims every tick — the
        # DLQ poller's two concerns keep independent cadences within one tick.
        dlq = _MaintDlqStore(purge_count=1)
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(
                auto_replay_enabled=True,
                retention=timedelta(hours=1),
                cleanup_interval=timedelta(hours=1),
                polling=_FAST,
            ),
        )
        async with make_async_container(_deps(dlq=dlq)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: dlq.claim_calls >= 3)  # replay claims every tick
            finally:
                await agent.stop()

        assert len(dlq.purged) == 1  # purge gated to once per interval


class TestPromotionPoller:
    # Ported from tests/messaging/inbox/test_promote_scheduled.py (ScheduledPromotionWorker subsumed).

    @staticmethod
    async def test_promotes_due_scheduled_rows() -> None:
        inbox = _MaintInboxStore(promote_count=2)
        config = MessagingConfig(inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)))
        async with make_async_container(_deps(inbox=inbox)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: inbox.promote_calls >= 1)
            finally:
                await agent.stop()

        assert inbox.promote_calls >= 1
