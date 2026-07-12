from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging import PollingConfig
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.errors.worker import DeadLetterWorker
from waku.messaging.router import MessageRouter
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingDeadLetterStore, make_codec, make_envelope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _DlqEvent(IEvent):
    value: str


class _RecordingEndpoint(Endpoint):
    def __init__(self, uri: str) -> None:
        super().__init__(uri)
        self.dispatched: list[MessageEnvelope[Any]] = []

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        self.dispatched.append(envelope)

    @override
    async def start(self) -> None: ...
    @override
    async def stop(self) -> None: ...


class _WorkerStore(RecordingDeadLetterStore):
    def __init__(self, claimable: Sequence[DeadLetterEntry] = (), purge_count: int = 0) -> None:
        super().__init__()
        self.replayed: list[UUID] = []
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
    async def mark_replayed(self, entry_id: UUID) -> None:
        self.replayed.append(entry_id)

    @override
    async def purge(self, older_than: datetime) -> int:
        self.purged.append(older_than)
        return self._purge_count


class _DlqDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: RecordingDeadLetterStore,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        router: MessageRouter,
        uow: IUnitOfWork,
    ) -> None:
        super().__init__()
        self._store = store
        self._codec = codec
        self._type_registry = type_registry
        self._router = router
        self._uow = uow

    @provide
    def store(self) -> IDeadLetterStore:
        return self._store

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide(scope=Scope.APP)
    def type_registry(self) -> MessageTypeRegistry:
        return self._type_registry

    @provide(scope=Scope.APP)
    def router(self) -> MessageRouter:
        return self._router

    replay_executor = provide(ReplayExecutor, scope=Scope.REQUEST)


def _make_registry() -> MessageTypeRegistry:
    return MessageTypeRegistry(identities={}, known_types=[_DlqEvent])


def _entry(destination: str) -> tuple[DeadLetterEntry, MessageEnvelope[Any]]:
    codec = make_codec()
    envelope = make_envelope(_DlqEvent('x'))
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination=destination,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
        metadata_=encode_metadata(envelope),
        group_id=envelope.group_id,
    )
    return entry, envelope


def _container(store: RecordingDeadLetterStore, router: MessageRouter, uow: IUnitOfWork) -> AsyncContainer:
    return make_async_container(
        _DlqDepsProvider(store, make_codec(), _make_registry(), router, uow),
    )


async def test_worker_replays_claimed_entries_and_commits() -> None:
    entry, _ = _entry('local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    store = _WorkerStore(claimable=[entry])
    uow = FakeUoW()
    container = _container(store, MessageRouter(routes={}, endpoints=[endpoint]), uow)
    worker = DeadLetterWorker(
        container=container,
        config=DeadLetterConfig(
            auto_replay_enabled=True,
            polling=PollingConfig(poll_interval_min_seconds=0.01),
        ),
    )
    await worker.start()
    await wait_until(lambda: bool(store.replayed))
    await worker.stop()

    assert store.replayed == [entry.id]
    # The DLQ entry's own id becomes message_id in the rebuilt envelope.
    assert endpoint.dispatched[0].message_id == entry.id
    assert uow.commit_count >= 1


async def test_worker_does_not_claim_when_auto_replay_disabled() -> None:
    # Force an observable tick (purge) so the absence-assertion below is not vacuous: the loop
    # demonstrably ran, yet claim_replayable was never called because auto_replay is gated off.
    entry, _ = _entry('local://dlq')
    store = _WorkerStore(claimable=[entry], purge_count=1)
    uow = FakeUoW()
    container = _container(store, MessageRouter(routes={}, endpoints=[]), uow)
    worker = DeadLetterWorker(
        container=container,
        config=DeadLetterConfig(
            auto_replay_enabled=False,
            retention=timedelta(days=30),
            cleanup_interval=timedelta(0),
            polling=PollingConfig(poll_interval_min_seconds=0.01),
        ),
    )
    await worker.start()
    await wait_until(lambda: bool(store.purged))
    await worker.stop()

    assert store.claim_calls == 0
    assert store.replayed == []


async def test_worker_purges_when_retention_set() -> None:
    store = _WorkerStore(purge_count=2)
    uow = FakeUoW()
    container = _container(store, MessageRouter(routes={}, endpoints=[]), uow)
    worker = DeadLetterWorker(
        container=container,
        config=DeadLetterConfig(
            auto_replay_enabled=False,
            retention=timedelta(days=30),
            cleanup_interval=timedelta(0),
            polling=PollingConfig(poll_interval_min_seconds=0.01),
        ),
    )
    await worker.start()
    await wait_until(lambda: bool(store.purged))
    await worker.stop()

    assert len(store.purged) >= 1


async def test_worker_does_not_purge_when_retention_none() -> None:
    # auto_replay drives observable ticks (claim_calls grows); with retention None the cleanup guard
    # must keep purge untouched. If the guard were removed, _maybe_cleanup would fault before the
    # replay step every tick, claim_calls would never advance, and the wait_until would time out.
    store = _WorkerStore(claimable=[])
    uow = FakeUoW()
    container = _container(store, MessageRouter(routes={}, endpoints=[]), uow)
    worker = DeadLetterWorker(
        container=container,
        config=DeadLetterConfig(
            auto_replay_enabled=True,
            retention=None,
            polling=PollingConfig(poll_interval_min_seconds=0.01),
        ),
    )
    await worker.start()
    await wait_until(lambda: store.claim_calls > 0)
    await worker.stop()

    assert store.purged == []
