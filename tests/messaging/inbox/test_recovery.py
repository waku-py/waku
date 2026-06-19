from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.recovery import InboxRecoveryWorker
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from datetime import datetime


class _TickCountingStore(FakeInboxStore):
    def __init__(self) -> None:
        super().__init__()
        self.recover_calls = 0

    @override
    async def recover_stale(self, threshold: timedelta) -> int:
        self.recover_calls += 1
        return await super().recover_stale(threshold)


class _RecoveryDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, store: IInboxStore) -> None:
        super().__init__()
        self._store = store
        self._uow: IUnitOfWork = FakeUoW()

    @provide
    def inbox(self) -> IInboxStore:
        return self._store

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


class TestInboxRecoveryWorker:
    @staticmethod
    async def test_worker_invokes_recover_stale_and_cleanup_each_tick() -> None:
        store = _TickCountingStore()
        config = InboxConfig(
            store=FakeInboxStore,
            stuck_threshold=timedelta(seconds=0),
            recovery_interval=timedelta(milliseconds=10),
            stop_timeout=1.0,
        )
        async with make_async_container(_RecoveryDepsProvider(store)) as container:
            worker = InboxRecoveryWorker(container=container, config=config)
            await worker.start()
            await wait_until(lambda: store.recover_calls >= 1)
            await worker.stop()

        assert worker.is_stopped is True
        assert store.recover_calls >= 1

    @staticmethod
    async def test_worker_can_be_stopped_when_never_started() -> None:
        config = InboxConfig(store=FakeInboxStore, stop_timeout=0.1)
        async with make_async_container(_RecoveryDepsProvider(FakeInboxStore())) as container:
            worker = InboxRecoveryWorker(container=container, config=config)
            await worker.stop()

        assert worker.is_stopped is True


class _RecordingDrainer:
    def __init__(self) -> None:
        self.drain_calls = 0

    async def drain_once(self) -> int:
        self.drain_calls += 1
        return 0


class _OrderStore(FakeInboxStore):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    @override
    async def recover_stale(self, threshold: timedelta) -> int:
        self._log.append('recover')
        return await super().recover_stale(threshold)

    @override
    async def cleanup_handled(self, now: datetime) -> int:
        self._log.append('cleanup')
        return await super().cleanup_handled(now)


class _OrderDrainer:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.drain_calls = 0

    async def drain_once(self) -> int:
        self._log.append('drain')
        self.drain_calls += 1
        return 0


def _config() -> InboxConfig:
    return InboxConfig(store=FakeInboxStore, recovery_interval=timedelta(seconds=0.01))


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


async def test_worker_without_drainer_runs_clean() -> None:
    inbox = FakeInboxStore()
    async with make_async_container(_RecoveryDepsProvider(inbox)) as container:
        worker = InboxRecoveryWorker(container=container, config=_config())
        await worker.start()
        await worker.stop()
    assert worker.is_stopped
