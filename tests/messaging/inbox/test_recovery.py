from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.transaction import RollbackFailedError, TransactionExecutionError
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox._internal.drainer import InboxDrainer
from waku.messaging.inbox._internal.recovery import InboxRecoveryWorker
from waku.messaging.inbox.config import InboxConfig
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from datetime import datetime


class _RecordingTickStore(FakeInboxStore):
    def __init__(self) -> None:
        super().__init__()
        self.recover_calls = 0

    @override
    async def recover_abandoned(self, threshold: timedelta) -> int:
        self.recover_calls += 1
        return await super().recover_abandoned(threshold)


class _RecoveryDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, store: IInboxStore, *, uow: IUnitOfWork | None = None) -> None:
        super().__init__()
        self._store = store
        self._uow = uow or RecordingUoW()

    @provide
    def inbox(self) -> IInboxStore:
        return self._store

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


class TestInboxRecoveryWorker:
    @staticmethod
    async def test_worker_invokes_recover_abandoned_and_cleanup_each_tick() -> None:
        store = _RecordingTickStore()
        config = InboxConfig(
            stuck_threshold=timedelta(seconds=0),
            recovery_interval=timedelta(milliseconds=10),
            stop_timeout=timedelta(seconds=1),
        )
        async with make_async_container(_RecoveryDepsProvider(store)) as container:
            worker = InboxRecoveryWorker(container=container, config=config, drainer=_RecordingDrainer())
            await worker.start()
            await wait_until(lambda: store.recover_calls >= 1)
            await worker.stop()

        assert worker.is_stopped is True
        assert store.recover_calls >= 1

    @staticmethod
    async def test_worker_can_be_stopped_when_never_started() -> None:
        config = InboxConfig(stop_timeout=timedelta(seconds=0.1))
        async with make_async_container(_RecoveryDepsProvider(FakeInboxStore())) as container:
            worker = InboxRecoveryWorker(container=container, config=config, drainer=_RecordingDrainer())
            await worker.stop()

        assert worker.is_stopped is True


class _RecordingDrainer(InboxDrainer):
    def __init__(self) -> None:
        # Bypass parent __init__: only drain_once is exercised.
        self.drain_calls = 0

    @override
    async def drain_once(self) -> int:
        self.drain_calls += 1
        return 0


class _TestableInboxRecoveryWorker(InboxRecoveryWorker):
    async def tick(self) -> int:
        return await super()._tick()


class _OrderStore(FakeInboxStore):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    @override
    async def recover_abandoned(self, threshold: timedelta) -> int:
        self._log.append('recover')
        return await super().recover_abandoned(threshold)

    @override
    async def delete_expired_handled(self, now: datetime) -> int:
        self._log.append('cleanup')
        return await super().delete_expired_handled(now)


class _OrderDrainer(InboxDrainer):
    def __init__(self, log: list[str]) -> None:
        # Bypass parent __init__: only drain_once is exercised.
        self._log = log
        self.drain_calls = 0

    @override
    async def drain_once(self) -> int:
        self._log.append('drain')
        self.drain_calls += 1
        return 0


class _CountingOrderStore(_OrderStore):
    @override
    async def recover_abandoned(self, threshold: timedelta) -> int:
        self._log.append('recover')
        return 2

    @override
    async def delete_expired_handled(self, now: datetime) -> int:
        self._log.append('cleanup')
        return 3


class _LoggingUoW(RecordingUoW):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    @override
    async def commit(self) -> None:
        await super().commit()
        self._log.append('commit')


def _config() -> InboxConfig:
    return InboxConfig(recovery_interval=timedelta(seconds=0.01))


async def test_worker_drains_each_tick() -> None:
    drainer = _RecordingDrainer()
    async with make_async_container(_RecoveryDepsProvider(FakeInboxStore())) as container:
        worker = InboxRecoveryWorker(container=container, config=_config(), drainer=drainer)
        await worker.start()
        await wait_until(lambda: drainer.drain_calls > 0)
        await worker.stop()
    assert drainer.drain_calls > 0


async def test_worker_recovers_before_draining() -> None:
    log: list[str] = []
    async with make_async_container(_RecoveryDepsProvider(_OrderStore(log))) as container:
        worker = InboxRecoveryWorker(container=container, config=_config(), drainer=_OrderDrainer(log))
        await worker.start()
        await wait_until(lambda: 'drain' in log)
        await worker.stop()
    assert log.index('recover') < log.index('drain')


async def test_recovery_and_cleanup_count_only_after_commit_then_drain() -> None:
    log: list[str] = []
    uow = _LoggingUoW(log)
    async with make_async_container(_RecoveryDepsProvider(_CountingOrderStore(log), uow=uow)) as container:
        worker = _TestableInboxRecoveryWorker(container=container, config=_config(), drainer=_OrderDrainer(log))
        assert await worker.tick() == 5

    assert log == ['recover', 'cleanup', 'commit', 'drain']
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


async def test_recovery_commit_failure_rolls_back_before_drain() -> None:
    commit_error = RuntimeError('recovery commit failed')
    uow = RecordingUoW(commit_error=commit_error)
    drainer = _RecordingDrainer()
    async with make_async_container(_RecoveryDepsProvider(FakeInboxStore(), uow=uow)) as container:
        worker = _TestableInboxRecoveryWorker(container=container, config=_config(), drainer=drainer)
        with pytest.raises(RuntimeError) as raised:
            await worker.tick()

    assert raised.value is commit_error
    assert uow.commit_count == 0
    assert uow.rollback_count == 1
    assert drainer.drain_calls == 0


async def test_recovery_rollback_failure_is_fatal_and_forbids_drain() -> None:
    commit_error = RuntimeError('recovery commit failed')
    rollback_error = RuntimeError('recovery rollback failed')
    uow = RecordingUoW(commit_error=commit_error, rollback_error=rollback_error)
    drainer = _RecordingDrainer()
    async with make_async_container(_RecoveryDepsProvider(FakeInboxStore(), uow=uow)) as container:
        worker = _TestableInboxRecoveryWorker(container=container, config=_config(), drainer=drainer)
        with pytest.raises(TransactionExecutionError) as raised:
            await worker.tick()

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is rollback_error
    assert raised.value.primary_error is commit_error
    assert drainer.drain_calls == 0
