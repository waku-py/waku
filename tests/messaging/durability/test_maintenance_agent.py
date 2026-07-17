from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from anyio.lowlevel import checkpoint
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.transaction import AfterCommitError, RollbackFailedError, TransactionExecutionError
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.messaging import PollingConfig
from waku.messaging._internal.maintenance import (
    DurabilityMaintenanceAgent,
    _DlqMaintenancePoller,
    _OutboxMaintenancePoller,
    _PromotionPoller,
)
from waku.messaging.config import DeadLetterConfig, MessagingConfig, OutboxConfig
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.errors._internal.replay import IReplayExecution
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry, DeadLetterStatus
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.outbox.relay import OutboxRelayConfig
from waku.messaging.sequence import ISequenceAllocator
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

_FAST = PollingConfig(poll_interval_min_seconds=0.01, poll_interval_max_seconds=0.05, poll_interval_step_seconds=0.01)


class _MaintOutboxStore(RecordingOutboxStore):
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
    async def recover_abandoned(self, threshold: timedelta) -> int:
        self.recover_calls += 1
        if self._recover_error is not None:
            err, self._recover_error = self._recover_error, None
            raise err
        return self._recover_count

    @override
    async def delete_expired_dispatched(self, older_than: timedelta) -> int:
        self.cleanup_calls += 1
        return self._cleanup_count


class _MaintDlqStore(InMemoryDeadLetterStore):
    def __init__(self, *, claimable: Sequence[DeadLetterEntry] = (), purge_count: int = 0) -> None:
        super().__init__()
        self.claim_calls = 0
        self.purged: list[tuple[datetime, datetime]] = []
        self._to_claim = list(claimable)
        self._purge_count = purge_count
        for entry in claimable:
            self.entries[entry.id] = entry

    @override
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        self.claim_calls += 1
        return await super().claim_replayable(
            max_replay_count,
            owner_id=owner_id,
            now=now,
            lease_expires_at=lease_expires_at,
        )

    @override
    async def delete_expired_dead_letters(self, older_than: datetime, *, now: datetime) -> int:
        self.purged.append((older_than, now))
        return self._purge_count


class _RepeatingMaintDlqStore(_MaintDlqStore):
    def __init__(self, *entries: DeadLetterEntry) -> None:
        super().__init__(claimable=entries)


class _MaintInboxStore(FakeInboxStore):
    def __init__(self, *, promote_count: int = 0) -> None:
        super().__init__()
        self.promote_calls = 0
        self._promote_count = promote_count

    @override
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        self.promote_calls += 1
        return self._promote_count


class _RecordingReplayExecutor(IReplayExecution):
    __slots__ = ('replayed',)

    def __init__(self) -> None:
        self.replayed: list[UUID] = []

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.replayed.append(entry.id)


class _BlockingReplayExecutor(IReplayExecution):
    __slots__ = ('_entered', '_released')

    def __init__(self, entered: anyio.Event, released: anyio.Event) -> None:
        self._entered = entered
        self._released = released

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self._entered.set()
        await self._released.wait()


class _CleanupFailingReplayExecutor(IReplayExecution):
    __slots__ = ('calls', 'rollback_error')

    def __init__(self, rollback_error: Exception) -> None:
        self.calls = 0
        self.rollback_error = rollback_error

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.calls += 1
        raise RollbackFailedError(
            self.rollback_error,
            RuntimeError('replay failed'),
        )


class _CompletedReplayExecutor(IReplayExecution):
    __slots__ = ('calls', 'teardown_error')

    def __init__(self, teardown_error: Exception) -> None:
        self.calls = 0
        self.teardown_error = teardown_error

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.calls += 1
        raise AfterCommitError(self.teardown_error)


class _CancellationCompletedReplayExecutor(IReplayExecution):
    __slots__ = ('calls', 'cancel_scope', 'cancellation_error')

    def __init__(self, cancel_scope: anyio.CancelScope, cancellation_error: BaseException) -> None:
        self.calls = 0
        self.cancel_scope = cancel_scope
        self.cancellation_error = cancellation_error

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.calls += 1
        raise AfterCommitError(self.cancellation_error)


class _PartialFailureReplayExecutor(IReplayExecution):
    __slots__ = ('calls', 'failure', 'successful_entry_id')

    def __init__(self, successful_entry_id: UUID, failure: BaseException) -> None:
        self.calls: list[UUID] = []
        self.successful_entry_id = successful_entry_id
        self.failure = failure

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.calls.append(entry.id)
        if entry.id == self.successful_entry_id:
            return
        raise self.failure


class _FailOnceReplayExecutor(IReplayExecution):
    __slots__ = ('calls', 'failure')

    def __init__(self, failure: Exception) -> None:
        self.calls = 0
        self.failure = failure

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self.calls += 1
        if self.calls == 1:
            raise self.failure


class _RetryOnceMaintDlqStore(_MaintDlqStore):
    def __init__(self, entry: DeadLetterEntry) -> None:
        super().__init__(claimable=[entry])


class _CheckpointingUoW(RecordingUoW):
    @override
    async def commit(self) -> None:
        await checkpoint()
        await super().commit()


class _TestableDlqMaintenancePoller(_DlqMaintenancePoller):
    async def tick(self) -> int:
        return await super()._tick()


class _TestableOutboxMaintenancePoller(_OutboxMaintenancePoller):
    async def tick(self) -> int:
        return await super()._tick()


class _TestablePromotionPoller(_PromotionPoller):
    async def tick(self) -> int:
        return await super()._tick()


class _MaintenanceDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        *,
        outbox: IOutboxStore,
        dlq: IDeadLetterStore,
        inbox: IInboxStore,
        replayer: IReplayExecution,
        uow: IUnitOfWork | None = None,
    ) -> None:
        super().__init__()
        self._outbox = outbox
        self._dlq = dlq
        self._inbox = inbox
        self._replayer = replayer
        self._allocator = RecordingAllocator()
        self._uow = uow or RecordingUoW()

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
    def replayer(self) -> IReplayExecution:
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
    replayer: IReplayExecution | None = None,
    uow: IUnitOfWork | None = None,
) -> _MaintenanceDepsProvider:
    return _MaintenanceDepsProvider(
        outbox=outbox or _MaintOutboxStore(),
        dlq=dlq or _MaintDlqStore(),
        inbox=inbox or _MaintInboxStore(),
        replayer=replayer or _RecordingReplayExecutor(),
        uow=uow,
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
        assert dlq.claim_calls == 2  # one committed claim, then one empty short claim after dispatch releases


class TestShutdownStopsEveryPoller:
    @staticmethod
    async def test_stop_stops_all_pollers_even_after_one_died_with_a_fatal() -> None:
        # One poller dead with a stored fatal must not strand its siblings: stop() has to stop every
        # poller, never abort the shutdown loop at the first re-raised fatal. The lone fatal surfaces by
        # identity, and the outbox poller (stopped after the dead DLQ poller in reverse order) is stopped.
        rollback_error = RuntimeError('replay rollback failed')
        replayer = _CleanupFailingReplayExecutor(rollback_error)
        provider = _MaintenanceDepsProvider(
            outbox=_MaintOutboxStore(recover_count=1),
            dlq=_MaintDlqStore(claimable=[_dlq_entry()]),
            inbox=_MaintInboxStore(promote_count=1),
            replayer=replayer,
        )
        async with make_async_container(provider) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=_all_three_config())
            await agent.start()
            await wait_until(lambda: replayer.calls >= 1)  # DLQ poller dispatched, then died with the fatal
            with pytest.raises(TransactionExecutionError) as raised:
                await agent.stop()

        outbox_poller = next(p for p in agent.pollers if isinstance(p, _OutboxMaintenancePoller))
        assert outbox_poller.is_stopped
        assert raised.value.error is rollback_error


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

    @staticmethod
    async def test_counts_recovery_and_cleanup_only_after_separate_commits() -> None:
        outbox = _MaintOutboxStore(recover_count=2, cleanup_count=3)
        uow = RecordingUoW()
        config = OutboxRelayConfig(
            recovery_interval=timedelta(seconds=0),
            retention=timedelta(hours=1),
            cleanup_interval=timedelta(seconds=0),
        )
        async with make_async_container(_deps(outbox=outbox, uow=uow)) as container:
            poller = _TestableOutboxMaintenancePoller(container=container, config=config)
            assert await poller.tick() == 5

        assert uow.commit_count == 2
        assert uow.rollback_count == 0

    @staticmethod
    async def test_recovery_commit_failure_rolls_back_and_skips_cleanup() -> None:
        commit_error = RuntimeError('maintenance recovery commit failed')
        outbox = _MaintOutboxStore(recover_count=2, cleanup_count=3)
        uow = RecordingUoW(commit_error=commit_error)
        config = OutboxRelayConfig(
            recovery_interval=timedelta(seconds=0),
            retention=timedelta(hours=1),
            cleanup_interval=timedelta(seconds=0),
        )
        async with make_async_container(_deps(outbox=outbox, uow=uow)) as container:
            poller = _TestableOutboxMaintenancePoller(container=container, config=config)
            with pytest.raises(RuntimeError) as raised:
                await poller.tick()

        assert raised.value is commit_error
        assert outbox.cleanup_calls == 0
        assert uow.commit_count == 0
        assert uow.rollback_count == 1


async def _assert_prefix_failure_stops(
    failure: TransactionExecutionError,
    *,
    expected_commits: int,
) -> None:
    first = _dlq_entry()
    second = _dlq_entry()
    replayer = _PartialFailureReplayExecutor(first.id, failure)
    uow = RecordingUoW()
    dlq = _RepeatingMaintDlqStore(first, second)
    config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

    async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
        poller = _TestableDlqMaintenancePoller(container=container, config=config)
        with pytest.raises(TransactionExecutionError) as raised:
            await poller.tick()

    assert raised.value is failure
    assert replayer.calls == [first.id, second.id]
    assert dlq.claim_calls == 2
    assert uow.commit_count == expected_commits
    assert uow.rollback_count == 0


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
    async def test_cleanup_failure_stops_replay_poller_and_escapes_by_identity() -> None:
        rollback_error = RuntimeError('rollback failed')
        replayer = _CleanupFailingReplayExecutor(rollback_error)
        config = MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST))

        async with make_async_container(
            _deps(dlq=_MaintDlqStore(claimable=[_dlq_entry()]), replayer=replayer),
        ) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            await wait_until(lambda: replayer.calls == 1)
            with pytest.raises(TransactionExecutionError) as raised:
                await agent.stop()

        assert isinstance(raised.value, RollbackFailedError)
        assert raised.value.error is rollback_error
        assert replayer.calls == 1

    @staticmethod
    async def test_post_commit_teardown_failure_commits_batch_then_stops_replay_poller() -> None:
        teardown_error = RuntimeError('replay scope teardown failed')
        replayer = _CompletedReplayExecutor(teardown_error)
        uow = RecordingUoW()
        config = MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST))

        async with make_async_container(
            _deps(dlq=_MaintDlqStore(claimable=[_dlq_entry()]), replayer=replayer, uow=uow),
        ) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            await wait_until(lambda: replayer.calls == 1)
            with pytest.raises(TransactionExecutionError) as raised:
                await agent.stop()

        assert isinstance(raised.value, AfterCommitError)
        assert raised.value.error is teardown_error
        assert replayer.calls == 1
        assert uow.commit_count == 2
        assert uow.rollback_count == 0

    @staticmethod
    async def test_post_commit_teardown_cancellation_shields_replay_batch_commit() -> None:
        cancel_scope = anyio.CancelScope()
        cancellation_error = anyio.get_cancelled_exc_class()()
        replayer = _CancellationCompletedReplayExecutor(cancel_scope, cancellation_error)
        uow = _CheckpointingUoW()
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(
            _deps(dlq=_MaintDlqStore(claimable=[_dlq_entry()]), replayer=replayer, uow=uow),
        ) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            with pytest.raises(TransactionExecutionError) as raised:
                await poller.tick()

        assert raised.value.error is cancellation_error
        assert replayer.calls == 1
        assert uow.commit_count == 2
        assert uow.rollback_count == 0

    @staticmethod
    async def test_claim_commit_failure_rolls_back_and_defers_replay() -> None:
        commit_error = RuntimeError('replay batch commit failed')
        replayer = _RecordingReplayExecutor()
        uow = RecordingUoW(commit_error=commit_error)
        dlq = _RepeatingMaintDlqStore(_dlq_entry())
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            assert await poller.tick() == 0

        assert replayer.replayed == []
        assert dlq.claim_calls == 1
        assert uow.commit_count == 0
        assert uow.rollback_count == 1

    @staticmethod
    async def test_successful_batch_prefix_then_plain_failure_finalizes_failed_and_continues() -> None:
        first = _dlq_entry()
        second = _dlq_entry()
        failure = RuntimeError('second replay failed')
        replayer = _PartialFailureReplayExecutor(first.id, failure)
        uow = RecordingUoW()
        dlq = _RepeatingMaintDlqStore(first, second)
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            assert await poller.tick() == 1

        assert replayer.calls == [first.id, second.id, second.id, second.id]
        assert dlq.claim_calls == 5
        assert dlq.entries[first.id].status is DeadLetterStatus.REPLAYED
        assert dlq.entries[second.id].status is DeadLetterStatus.REPLAY_FAILED
        assert dlq.entries[second.id].replay_count == 3
        assert failure.args[0] in (dlq.entries[second.id].error_message or '')
        assert uow.commit_count == 9
        assert uow.rollback_count == 0

    @staticmethod
    async def test_successful_batch_prefix_then_cancellation_commits_prefix_and_stops() -> None:
        cancellation_error = anyio.get_cancelled_exc_class()()
        first = _dlq_entry()
        second = _dlq_entry()
        replayer = _PartialFailureReplayExecutor(first.id, cancellation_error)
        uow = RecordingUoW()
        dlq = _RepeatingMaintDlqStore(first, second)
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            with pytest.raises(anyio.get_cancelled_exc_class()) as raised:
                await poller.tick()

        assert raised.value is cancellation_error
        assert replayer.calls == [first.id, second.id]
        assert dlq.entries[first.id].status is DeadLetterStatus.REPLAYED
        assert dlq.entries[second.id].status is DeadLetterStatus.REPLAY_FAILED
        assert uow.commit_count == 4
        assert uow.rollback_count == 0

    @staticmethod
    async def test_successful_batch_prefix_then_completed_execution_commits_and_stops() -> None:
        teardown_error = RuntimeError('second replay scope teardown failed')
        failure = AfterCommitError(teardown_error)
        await _assert_prefix_failure_stops(
            failure,
            expected_commits=4,
        )

    @staticmethod
    async def test_successful_batch_prefix_then_cleanup_failure_rolls_back_and_stops() -> None:
        rollback_error = RuntimeError('second replay rollback failed')
        failure = RollbackFailedError(
            rollback_error,
            RuntimeError('second replay failed'),
        )
        await _assert_prefix_failure_stops(
            failure,
            expected_commits=3,
        )

    @staticmethod
    async def test_mixed_cancellation_group_preserves_group_and_committed_prefix() -> None:
        first = _dlq_entry()
        second = _dlq_entry()
        cancellation = anyio.get_cancelled_exc_class()()
        fatal = RollbackFailedError(
            RuntimeError('rollback failed'),
            RuntimeError('handler failed'),
        )
        failure = BaseExceptionGroup('mixed replay failure', [cancellation, fatal])
        replayer = _PartialFailureReplayExecutor(first.id, failure)
        uow = RecordingUoW()
        dlq = _RepeatingMaintDlqStore(first, second)
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            with pytest.raises(BaseExceptionGroup) as raised:
                await poller.tick()

        assert raised.value is failure
        assert raised.value.exceptions == (cancellation, fatal)
        assert replayer.calls == [first.id, second.id]
        assert dlq.claim_calls == 2
        assert dlq.entries[first.id].status is DeadLetterStatus.REPLAYED
        assert dlq.entries[second.id].status is DeadLetterStatus.PENDING
        assert uow.commit_count == 3
        assert uow.rollback_count == 0

    @staticmethod
    async def test_fatal_only_group_unwraps_without_causal_mutation() -> None:
        entry = _dlq_entry()
        fatal = RollbackFailedError(
            RuntimeError('rollback failed'),
            RuntimeError('handler failed'),
        )
        replayer = _PartialFailureReplayExecutor(uuid4(), BaseExceptionGroup('fatal replay failure', [fatal]))
        uow = RecordingUoW()
        dlq = _RepeatingMaintDlqStore(entry)
        config = DeadLetterConfig(auto_replay_enabled=True, polling=_FAST)

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config)
            with pytest.raises(TransactionExecutionError) as raised:
                await poller.tick()

        assert raised.value is fatal
        assert fatal.__cause__ is None
        assert fatal.__context__ is None
        assert replayer.calls == [entry.id]
        assert dlq.claim_calls == 1
        assert uow.commit_count == 1
        assert uow.rollback_count == 0

    @staticmethod
    async def test_first_plain_failure_remains_recoverable_then_commits_retry() -> None:
        entry = _dlq_entry()
        failure = RuntimeError('first replay failed')
        replayer = _FailOnceReplayExecutor(failure)
        uow = RecordingUoW()
        dlq = _RetryOnceMaintDlqStore(entry)
        config = MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST))

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            try:
                await wait_until(lambda: replayer.calls == 2)
            finally:
                await agent.stop()

        assert replayer.calls == 2
        assert dlq.claim_calls == 3
        assert uow.commit_count == 5
        assert uow.rollback_count == 0

    @staticmethod
    async def test_first_cancellation_rolls_back_and_stops_without_retry() -> None:
        entry = _dlq_entry()
        cancellation_error = anyio.get_cancelled_exc_class()()
        replayer = _PartialFailureReplayExecutor(uuid4(), cancellation_error)
        uow = RecordingUoW()
        dlq = _RepeatingMaintDlqStore(entry)
        config = MessagingConfig(dead_letter=DeadLetterConfig(auto_replay_enabled=True, polling=_FAST))

        async with make_async_container(_deps(dlq=dlq, replayer=replayer, uow=uow)) as container:
            agent = DurabilityMaintenanceAgent(container=container, config=config)
            await agent.start()
            await wait_until(lambda: len(replayer.calls) == 1)
            with pytest.raises(anyio.get_cancelled_exc_class()) as raised:
                await agent.stop()

        assert raised.value is cancellation_error
        assert replayer.calls == [entry.id]
        assert dlq.claim_calls == 1
        assert uow.commit_count == 2
        assert uow.rollback_count == 0

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
    async def test_cleanup_samples_clock_once_for_cutoff_and_lease_validity() -> None:
        sampled = datetime(2026, 7, 16, 12, tzinfo=UTC)
        clock_calls = 0

        def now() -> datetime:
            nonlocal clock_calls
            clock_calls += 1
            return sampled

        dlq = _MaintDlqStore(purge_count=2)
        config = DeadLetterConfig(
            auto_replay_enabled=False,
            retention=timedelta(days=30),
            cleanup_interval=timedelta(seconds=0),
            polling=_FAST,
        )
        async with make_async_container(_deps(dlq=dlq)) as container:
            poller = _TestableDlqMaintenancePoller(container=container, config=config, now=now)
            assert await poller.tick() == 2

        assert clock_calls == 1
        assert dlq.purged == [(sampled - timedelta(days=30), sampled)]

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

    @staticmethod
    async def test_counts_promotions_only_after_commit() -> None:
        inbox = _MaintInboxStore(promote_count=3)
        uow = RecordingUoW()
        async with make_async_container(_deps(inbox=inbox, uow=uow)) as container:
            poller = _TestablePromotionPoller(container=container, config=InboxConfig())
            assert await poller.tick() == 3

        assert uow.commit_count == 1
        assert uow.rollback_count == 0

    @staticmethod
    async def test_promotion_rollback_failure_is_fatal() -> None:
        commit_error = RuntimeError('promotion commit failed')
        rollback_error = RuntimeError('promotion rollback failed')
        uow = RecordingUoW(commit_error=commit_error, rollback_error=rollback_error)
        async with make_async_container(_deps(inbox=_MaintInboxStore(promote_count=3), uow=uow)) as container:
            poller = _TestablePromotionPoller(container=container, config=InboxConfig())
            with pytest.raises(TransactionExecutionError) as raised:
                await poller.tick()

        assert isinstance(raised.value, RollbackFailedError)
        assert raised.value.error is rollback_error
        assert raised.value.primary_error is commit_error
