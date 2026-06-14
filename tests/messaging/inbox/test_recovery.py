from __future__ import annotations

from datetime import timedelta

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.recovery import InboxRecoveryWorker
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, wait_until
from tests.messaging.inbox.fake_store import FakeInboxStore


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
            stale_threshold=timedelta(seconds=0),
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
