from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import anyio
import pytest
from anyio.lowlevel import checkpoint
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.lease import LeaseConfig
from waku._internal.transaction import RollbackFailedError, TransactionExecutionError
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    HandlerMap,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging._internal.ownership import AppScopeSource
from waku.messaging.config import DeadLetterConfig, OutboxConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayClaimOwner, ReplayExecution
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.router import MessageRouter, external_endpoint, listen
from waku.messaging.sequence import ISequenceAllocator
from waku.messaging.transport import MalformedMetadataError
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDurabilityStore,
    RecordingTransport,
    RecordingUoW,
    make_codec,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.backends.memory._internal.outbox import WorkspaceOutboxStore
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.outbox.models import OutboxMessage


def test_dead_letter_replay_lease_pair_and_default_config() -> None:
    now = datetime.now(tz=UTC)
    envelope = make_envelope(_DlqEvent('lease'))
    entry = _entry_for(envelope, 'local://lease')

    with pytest.raises(
        ValueError, match='replay_owner_id and replay_lease_expires_at must both be set or both be None'
    ):
        replace(entry, replay_owner_id='owner')
    with pytest.raises(
        ValueError, match='replay_owner_id and replay_lease_expires_at must both be set or both be None'
    ):
        replace(entry, replay_lease_expires_at=now + timedelta(minutes=1))

    leased = replace(
        entry,
        replay_owner_id='owner',
        replay_lease_expires_at=now + timedelta(minutes=1),
    )
    assert leased.replay_owner_id == 'owner'
    assert leased.replay_lease_expires_at == now + timedelta(minutes=1)
    assert DeadLetterConfig().replay_lease == LeaseConfig(ttl_seconds=120.0)


@dataclass(frozen=True, slots=True)
class _DlqEvent(IEvent):
    value: str


_handled: list[str] = []


class _DlqEventHandler(EventHandler[_DlqEvent]):
    @override
    async def handle(self, event: _DlqEvent, /) -> None:
        _handled.append(event.value)


class _RecordingEndpoint(Endpoint):
    def __init__(self, uri: str, *, boom: bool = False) -> None:
        super().__init__(uri, MessageObservers([]))
        self.dispatched: list[MessageEnvelope[Any]] = []
        self._boom = boom

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        if self._boom:
            msg = 'dispatch boom'
            raise RuntimeError(msg)
        self.dispatched.append(envelope)

    @override
    async def start(self) -> None: ...
    @override
    async def stop(self) -> None: ...


class _ReplayStore(InMemoryDeadLetterStore):
    def __init__(self, entry: DeadLetterEntry | None = None) -> None:
        super().__init__()
        self.replayed: list[UUID] = []
        self.failures: list[tuple[UUID, str]] = []
        if entry is not None:
            self.entries[entry.id] = entry

    @override
    async def mark_replayed(self, entry_id: UUID, *, owner_id: str, now: datetime) -> bool:
        marked = await super().mark_replayed(entry_id, owner_id=owner_id, now=now)
        if marked:
            self.replayed.append(entry_id)
        return marked

    @override
    async def mark_replay_failed(
        self,
        entry_id: UUID,
        error: str,
        *,
        owner_id: str,
        now: datetime,
    ) -> bool:
        marked = await super().mark_replay_failed(entry_id, error, owner_id=owner_id, now=now)
        if marked:
            self.failures.append((entry_id, error))
        return marked


def _durability(
    unit_of_work: IUnitOfWork,
    outbox: IOutboxStore,
    inbox: IInboxStore,
    dead_letters: IDeadLetterStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=inbox,
        dead_letters=dead_letters,
    )


def _fresh_uow() -> RecordingUoW:
    return RecordingUoW()


class _SentSink:
    def __init__(self) -> None:
        self.destinations: list[str] = []


class _SentObserver(IMessageObserver):
    def __init__(self, sink: _SentSink) -> None:
        self._sink = sink

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self._sink.destinations.append(destination)


class _OutboxUnavailableError(Exception):
    pass


class _FailingOutboxStore(RecordingOutboxStore):
    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        raise _OutboxUnavailableError


class _FailingReplayMarkStore(_ReplayStore):
    def __init__(self, mark_error: Exception, entry: DeadLetterEntry | None = None) -> None:
        super().__init__(entry)
        self.mark_calls = 0
        self.mark_error = mark_error

    @override
    async def mark_replayed(self, entry_id: UUID, *, owner_id: str, now: datetime) -> bool:
        self.mark_calls += 1
        raise self.mark_error


class _LosingRenewalStore(_ReplayStore):
    def __init__(self, entry: DeadLetterEntry) -> None:
        super().__init__(entry)
        self.renew_calls = 0

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.renew_calls += 1
        return False


class _FailingRenewalStore(_ReplayStore):
    def __init__(self, entry: DeadLetterEntry, error: Exception) -> None:
        super().__init__(entry)
        self.error = error

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        raise self.error


class _GatedRenewalStore(_ReplayStore):
    def __init__(self, entry: DeadLetterEntry, renewal_started: anyio.Event, release_renewal: anyio.Event) -> None:
        super().__init__(entry)
        self.renewal_started = renewal_started
        self.release_renewal = release_renewal
        self.renewal_cancelled = False
        self.trace: list[str] = []

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.trace.append('renewal-started')
        self.renewal_started.set()
        try:
            await self.release_renewal.wait()
        except BaseException:
            self.renewal_cancelled = True
            raise
        renewed = await super().renew_replay_claim(
            entry_id,
            owner_id=owner_id,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        self.trace.append('renewal-completed')
        return renewed

    @override
    async def mark_replayed(self, entry_id: UUID, *, owner_id: str, now: datetime) -> bool:
        self.trace.append('finalization-started')
        return await super().mark_replayed(entry_id, owner_id=owner_id, now=now)


class _BlockingReplayExecution(IReplayExecution):
    def __init__(self, entered: anyio.Event, release: anyio.Event) -> None:
        self._entered = entered
        self._release = release

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        self._entered.set()
        await self._release.wait()


class _SignallingReplayExecution(IReplayExecution):
    def __init__(self, release: anyio.Event, completed: anyio.Event) -> None:
        self._release = release
        self._completed = completed

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        await self._release.wait()
        self._completed.set()


class _RaisingReplayExecution(IReplayExecution):
    def __init__(self, error: BaseException) -> None:
        self._error = error

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        raise self._error


class _ReplayOwnerDeps(Provider):
    scope = Scope.REQUEST

    def __init__(self, store: IDeadLetterStore, uow: IUnitOfWork) -> None:
        super().__init__()
        self._store = store
        self._uow = uow

    @provide
    def dead_letters(self) -> IDeadLetterStore:
        return self._store

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 16, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self._now
        self._now += timedelta(milliseconds=5)
        return current


_DUMMY_CONTAINER: Any = object()  # endpoints under test ignore the scope arg
_DUMMY_DISPATCHER: Any = object()  # ENDPOINT-branch tests never reach the handler dispatch
_DUMMY_SCOPES: Any = object()


def _make_type_registry() -> MessageTypeRegistry:
    return MessageTypeRegistry(identities={}, known_types=[_DlqEvent])


def _entry_for(
    envelope: MessageEnvelope[Any],
    destination: str,
    kind: DeadLetterDestinationKind = DeadLetterDestinationKind.ENDPOINT,
) -> DeadLetterEntry:
    codec = make_codec()
    return DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination=destination,
        destination_kind=kind,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
        message_id=envelope.message_id,
        metadata=encode_metadata(envelope),
        group_id=envelope.group_id,
    )


def _make_executor(endpoint: Endpoint | None) -> ReplayExecution:
    endpoints = [endpoint] if endpoint is not None else []
    return ReplayExecution(
        container=_DUMMY_CONTAINER,
        codec=make_codec(),
        type_registry=_make_type_registry(),
        router=MessageRouter(routes={}, endpoints=endpoints),
        dispatcher=_DUMMY_DISPATCHER,
        handler_map=HandlerMap(),
        app_scope=_DUMMY_SCOPES,
    )


def _replay_executor(container: AsyncContainer, execution: IReplayExecution) -> ReplayExecutor:
    return ReplayExecutor(
        execution=execution,
        config=DeadLetterConfig(),
        app_scope=AppScopeSource(container),
        now=_AdvancingClock(),
    )


async def test_replay_reinjects_to_destination_preserving_original_message_id() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    executor = _make_executor(endpoint)

    await executor.dispatch(entry)
    assert len(endpoint.dispatched) == 1
    # The original envelope message_id is preserved through the DLQ message_id column.
    assert endpoint.dispatched[0].message_id == envelope.message_id


async def test_replay_unknown_destination_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://gone')
    executor = _make_executor(endpoint=None)

    with pytest.raises(RuntimeError, match='local://gone'):
        await executor.dispatch(entry)


async def test_replay_bidirectional_endpoint_dispatches() -> None:
    # A real bidirectional endpoint (external_endpoint + listen on the same URI) merges into
    # ONE MergedBrokerEndpoint carrying both aspects. Runs the real _build_router send-filter
    # (`isinstance(entry, LocalQueueEntry) or entry.send is not None`) through create_test_app:
    # if the filter regressed to exclude listen+send endpoints, endpoint_for would return None
    # and replay would mark the entry failed instead of dispatching.
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='rabbitmq://orders')
    dlq_store = _ReplayStore()
    inbox_store = FakeInboxStore()
    config = MessagingConfig(
        endpoints=[external_endpoint('rabbitmq://orders'), listen('rabbitmq://orders')],
        outbox=OutboxConfig(),
        inbox=InboxConfig(owner_id='test-node:1'),
        dead_letter=DeadLetterConfig(),
        transports={'rabbitmq': RecordingTransport},
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox_store, provided_type=IInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IOutboxStore, RecordingOutboxStore),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        await dlq_store.save(entry)
        replayer = await scope.get(ReplayExecutor)

        assert await replayer.replay(entry) is True

    assert dlq_store.replayed == [entry.id]
    assert dlq_store.failures == []


async def test_replay_listen_only_endpoint_marks_failed() -> None:
    # A listen-only endpoint (no send aspect) is excluded from router.endpoints by the
    # send-filter in _build_router, so endpoint_for returns None here — not replayable.
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='rabbitmq://orders')
    executor = _make_executor(endpoint=None)

    with pytest.raises(RuntimeError, match='rabbitmq://orders'):
        await executor.dispatch(entry)


async def test_replay_handler_kind_dispatches_resolved_handler() -> None:
    # An inbox-origin (HANDLER-kind) dead letter names a handler FQN — never a router URI. Replay
    # must resolve the ONE handler and reprocess it inline (B-10 fixed): handler runs, mark_replayed.
    _handled.clear()
    envelope = make_envelope(_DlqEvent('reprocessed'))
    entry = _entry_for(
        envelope,
        destination=handler_destination(_DlqEventHandler),
        kind=DeadLetterDestinationKind.HANDLER,
    )
    dlq_store = _ReplayStore()
    config = MessagingConfig(dead_letter=DeadLetterConfig())

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                scoped(IOutboxStore, RecordingOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        await dlq_store.save(entry)
        replayer = await scope.get(ReplayExecutor)

        assert await replayer.replay(entry) is True

    assert _handled == ['reprocessed']
    assert dlq_store.replayed == [entry.id]
    assert dlq_store.failures == []


async def test_replay_handler_rollback_failure_escapes_instead_of_marking_replay_failed() -> None:
    class FailingHandler(EventHandler[_DlqEvent]):
        @override
        async def handle(self, _event: _DlqEvent, /) -> None:
            msg = 'handler failed'
            raise ValueError(msg)

    envelope = make_envelope(_DlqEvent('rollback-failure'))
    entry = _entry_for(
        envelope,
        destination=handler_destination(FailingHandler),
        kind=DeadLetterDestinationKind.HANDLER,
    )
    rollback_error = RuntimeError('rollback failed')
    dlq_store = _ReplayStore()
    config = MessagingConfig(
        dead_letter=DeadLetterConfig(),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(FailingHandler)],
            providers=[
                object_(RecordingUoW(rollback_error=rollback_error), provided_type=IUnitOfWork),
                scoped(IOutboxStore, RecordingOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        await dlq_store.save(entry)
        replayer = await scope.get(ReplayExecutor)
        with pytest.raises(RuntimeError) as raised:
            await replayer.replay(entry)

    assert raised.value is rollback_error
    assert dlq_store.replayed == []
    assert dlq_store.failures == []


async def test_replay_post_dispatch_mark_failure_is_not_reported_as_handler_failure() -> None:
    calls: list[str] = []
    mark_error = RuntimeError('mark replayed failed')
    uow = RecordingUoW()

    class SuccessfulHandler(EventHandler[_DlqEvent]):
        @override
        async def handle(self, event: _DlqEvent, /) -> None:
            calls.append(event.value)

    envelope = make_envelope(_DlqEvent('post-commit-mark-failure'))
    entry = _entry_for(
        envelope,
        destination=handler_destination(SuccessfulHandler),
        kind=DeadLetterDestinationKind.HANDLER,
    )
    dlq_store = _FailingReplayMarkStore(mark_error)
    config = MessagingConfig(
        dead_letter=DeadLetterConfig(),
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(SuccessfulHandler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                scoped(IOutboxStore, RecordingOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        await dlq_store.save(entry)
        replayer = await scope.get(ReplayExecutor)
        with pytest.raises(RuntimeError) as raised:
            await replayer.replay(entry)

    assert raised.value is mark_error
    assert calls == ['post-commit-mark-failure']
    assert uow.commit_count == 2
    assert uow.rollback_count == 1
    assert dlq_store.mark_calls == 1
    assert dlq_store.replayed == []
    assert dlq_store.failures == []


async def test_long_dispatch_renews_in_fresh_transactions_before_success_finalization() -> None:
    envelope = make_envelope(_DlqEvent('renew'))
    entry = _entry_for(envelope, destination='local://dlq')
    store = _ReplayStore(entry)
    uow = RecordingUoW()
    clock = _AdvancingClock()
    config = DeadLetterConfig(replay_lease=LeaseConfig(ttl_seconds=0.03))
    entered = anyio.Event()
    release = anyio.Event()
    result: list[bool] = []

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        owner = ReplayClaimOwner(container=container, config=config, now=clock)
        claimed = await owner.claim_replay(entry.id)
        assert claimed is not None
        initial_expiry = claimed.replay_lease_expires_at

        async def replay() -> None:
            result.append(await owner.replay_claimed(claimed, _BlockingReplayExecution(entered, release)))

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(replay)
            await entered.wait()
            await anyio.sleep(config.replay_lease.renew_interval_seconds * 1.5)
            renewed_expiry = store.entries[entry.id].replay_lease_expires_at
            assert renewed_expiry is not None
            assert initial_expiry is not None
            assert renewed_expiry > initial_expiry
            assert store.replayed == []
            release.set()

    assert result == [True]
    assert store.replayed == [entry.id]
    assert uow.commit_count >= 3  # claim, at least one renewal, finalization


async def test_successful_dispatch_waits_for_started_renewal_before_finalization() -> None:
    envelope = make_envelope(_DlqEvent('renewal-completion-race'))
    entry = _entry_for(envelope, destination='local://dlq')
    renewal_started = anyio.Event()
    release_renewal = anyio.Event()
    release_dispatch = anyio.Event()
    dispatch_completed = anyio.Event()
    store = _GatedRenewalStore(entry, renewal_started, release_renewal)
    uow = RecordingUoW()
    config = DeadLetterConfig(replay_lease=LeaseConfig(ttl_seconds=0.03))
    result: list[bool] = []
    errors: list[BaseException] = []

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        owner = ReplayClaimOwner(container=container, config=config, now=_AdvancingClock())
        claimed = await owner.claim_replay(entry.id)
        assert claimed is not None

        async def replay() -> None:
            try:
                result.append(
                    await owner.replay_claimed(
                        claimed,
                        _SignallingReplayExecution(release_dispatch, dispatch_completed),
                    ),
                )
            except BaseException as error:  # noqa: BLE001 -- the regression observes cancellation replacement
                errors.append(error)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(replay)
            await renewal_started.wait()
            release_dispatch.set()
            await dispatch_completed.wait()
            await checkpoint()
            release_renewal.set()

    assert errors == []
    assert result == [True]
    assert store.renewal_cancelled is False
    assert store.trace == ['renewal-started', 'renewal-completed', 'finalization-started']
    assert store.replayed == [entry.id]
    assert store.failures == []
    assert uow.commit_count == 3
    assert uow.rollback_count == 0


async def test_manual_replay_preserves_mixed_cancellation_group_and_leaf_identities() -> None:
    envelope = make_envelope(_DlqEvent('mixed-control-flow'))
    entry = _entry_for(envelope, destination='local://dlq')
    store = _ReplayStore(entry)
    uow = RecordingUoW()
    cancellation = anyio.get_cancelled_exc_class()()
    fatal = RollbackFailedError(RuntimeError('rollback failed'), RuntimeError('handler failed'))
    failure = BaseExceptionGroup('mixed replay failure', [cancellation, fatal])

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        replayer = _replay_executor(container, _RaisingReplayExecution(failure))
        with pytest.raises(BaseExceptionGroup) as raised:
            await replayer.replay(entry)

    assert raised.value is failure
    assert raised.value.exceptions == (cancellation, fatal)
    assert store.replayed == []
    assert store.failures == []
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


async def test_manual_replay_fatal_only_group_uses_public_translation_without_mutating_fatal() -> None:
    envelope = make_envelope(_DlqEvent('fatal-only-group'))
    entry = _entry_for(envelope, destination='local://dlq')
    store = _ReplayStore(entry)
    uow = RecordingUoW()
    rollback_error = RuntimeError('rollback failed')
    handler_error = RuntimeError('handler failed')
    fatal = RollbackFailedError(rollback_error, handler_error)
    failure = BaseExceptionGroup('fatal replay failure', [fatal])

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        replayer = _replay_executor(container, _RaisingReplayExecution(failure))
        with pytest.raises(RuntimeError) as raised:
            await replayer.replay(entry)

    assert raised.value is rollback_error
    assert raised.value.__cause__ is handler_error
    assert fatal.__cause__ is None
    assert fatal.__context__ is None
    assert store.replayed == []
    assert store.failures == []
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


async def test_manual_replay_external_cancellation_remains_primary_after_shielded_finalization() -> None:
    envelope = make_envelope(_DlqEvent('external-cancellation'))
    entry = _entry_for(envelope, destination='local://dlq')
    store = _ReplayStore(entry)
    uow = RecordingUoW()
    entered = anyio.Event()
    cancel_scope = anyio.CancelScope()
    errors: list[BaseException] = []

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        replayer = _replay_executor(container, _BlockingReplayExecution(entered, anyio.Event()))

        async def replay() -> None:
            with cancel_scope:
                try:
                    await replayer.replay(entry)
                except BaseException as error:  # noqa: BLE001 -- cancellation identity is the behavior under test
                    errors.append(error)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(replay)
            await entered.wait()
            cancel_scope.cancel()

    assert len(errors) == 1
    assert isinstance(errors[0], anyio.get_cancelled_exc_class())
    assert store.replayed == []
    assert len(store.failures) == 1
    assert uow.commit_count == 2
    assert uow.rollback_count == 0


async def test_lost_renewal_cancels_dispatch_without_success_finalization() -> None:
    envelope = make_envelope(_DlqEvent('lost-renewal'))
    entry = _entry_for(envelope, destination='local://dlq')
    store = _LosingRenewalStore(entry)
    uow = RecordingUoW()
    config = DeadLetterConfig(replay_lease=LeaseConfig(ttl_seconds=0.03))
    entered = anyio.Event()

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        owner = ReplayClaimOwner(container=container, config=config, now=_AdvancingClock())
        claimed = await owner.claim_replay(entry.id)
        assert claimed is not None
        with anyio.fail_after(1), pytest.raises(TransactionExecutionError) as raised:
            await owner.replay_claimed(claimed, _BlockingReplayExecution(entered, anyio.Event()))

    assert 'Replay claim ownership was lost' in str(raised.value.error)
    assert store.renew_calls == 1
    assert store.replayed == []
    assert store.entries[entry.id].replay_owner_id == owner.owner_id


async def test_ordinary_renewal_failure_rolls_back_then_finalizes_failed() -> None:
    envelope = make_envelope(_DlqEvent('renewal-error'))
    entry = _entry_for(envelope, destination='local://dlq')
    renewal_error = ConnectionError('renewal backend unavailable')
    store = _FailingRenewalStore(entry, renewal_error)
    uow = RecordingUoW()
    config = DeadLetterConfig(replay_lease=LeaseConfig(ttl_seconds=0.03))

    async with make_async_container(_ReplayOwnerDeps(store, uow)) as container:
        owner = ReplayClaimOwner(container=container, config=config, now=_AdvancingClock())
        claimed = await owner.claim_replay(entry.id)
        assert claimed is not None
        replayed = await owner.replay_claimed(
            claimed,
            _BlockingReplayExecution(anyio.Event(), anyio.Event()),
        )

    assert replayed is False
    assert store.replayed == []
    assert len(store.failures) == 1
    assert 'renewal backend unavailable' in store.failures[0][1]
    assert uow.commit_count == 2
    assert uow.rollback_count == 1


async def test_replay_handler_kind_unknown_fqn_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(
        envelope,
        destination='tests.messaging.NoSuchHandler',
        kind=DeadLetterDestinationKind.HANDLER,
    )
    executor = _make_executor(endpoint=None)

    with pytest.raises(RuntimeError, match=r'tests\.messaging\.NoSuchHandler'):
        await executor.dispatch(entry)


async def test_replay_dispatch_error_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq', boom=True)
    executor = _make_executor(endpoint)

    with pytest.raises(RuntimeError, match='dispatch boom'):
        await executor.dispatch(entry)


async def test_replay_corrupt_metadata_blob_marks_replay_failed() -> None:
    # A dead-letter row with a corrupt metadata blob (non-integer message_version) makes
    # wire_metadata_from_entry raise MalformedMetadataError — the replay worker's broad net records
    # REPLAY_FAILED and returns False instead of letting the raise reach the worker loop.
    envelope = make_envelope(_DlqEvent('hi'))
    entry = replace(
        _entry_for(envelope, destination='local://dlq'),
        metadata={'message_version': 'abc', 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {}},
    )
    endpoint = _RecordingEndpoint('local://dlq')
    executor = _make_executor(endpoint)

    with pytest.raises(MalformedMetadataError):
        await executor.dispatch(entry)
    assert endpoint.dispatched == []  # never dispatched — poison caught before the endpoint


async def test_internal_replay_execution_is_dispatch_only() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    executor = _make_executor(endpoint)

    assert not hasattr(executor, 'replay')
    assert not hasattr(executor, 'replay_by_id')
    await executor.dispatch(entry)


async def test_replay_reconstruct_and_compare_all_metadata_fields() -> None:
    scheduled = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    expires = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    envelope = make_envelope(
        _DlqEvent('payload'),
        headers={'x-trace': 'abc123', 'x-tenant': 'acme'},
        group_id='order-99',
        scheduled_time=scheduled,
        expires_at=expires,
    )
    codec = make_codec()
    type_registry = _make_type_registry()
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination='local://dlq',
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='failed',
        retry_count=5,
        message_id=envelope.message_id,
        metadata=encode_metadata(envelope),
        group_id=envelope.group_id,
    )

    endpoint = _RecordingEndpoint('local://dlq')
    executor = ReplayExecution(
        container=_DUMMY_CONTAINER,
        codec=codec,
        type_registry=type_registry,
        router=MessageRouter(routes={}, endpoints=[endpoint]),
        dispatcher=_DUMMY_DISPATCHER,
        handler_map=HandlerMap(),
        app_scope=_DUMMY_SCOPES,
    )

    await executor.dispatch(entry)
    assert len(endpoint.dispatched) == 1
    rebuilt = endpoint.dispatched[0]

    # Non-vacuous: all metadata fields must round-trip correctly.
    # message_id is preserved from the original envelope via the DLQ message_id column.
    assert rebuilt.message_id == envelope.message_id
    assert rebuilt.correlation_id == envelope.correlation_id
    assert rebuilt.causation_id == envelope.causation_id
    assert rebuilt.message_type == envelope.message_type
    assert rebuilt.message_version == envelope.message_version
    assert rebuilt.headers == envelope.headers
    assert rebuilt.group_id == envelope.group_id
    assert rebuilt.payload == envelope.payload
    # Timestamps normalised to UTC — compare with tolerance for isoformat round-trip.
    assert rebuilt.timestamp is not None
    assert abs((rebuilt.timestamp - envelope.timestamp).total_seconds()) < 1
    assert rebuilt.scheduled_time is not None
    assert abs((rebuilt.scheduled_time - scheduled).total_seconds()) < 1
    assert rebuilt.expires_at is not None
    assert abs((rebuilt.expires_at - expires).total_seconds()) < 1


async def test_endpoint_replay_commits_restaged_outbox_row_and_fires_sent() -> None:
    # CRIT2.1, replay leg: replaying an ENDPOINT-kind dead letter to an outbox-backed endpoint stages the
    # row inside a committed, isolated APP-scope transaction (so it survives request-scope teardown) and
    # fires `sent` only after that commit. Pre-fix the stage ran on the ambient request scope with no
    # commit, so the row was discarded on teardown and no `sent` was emitted. Wired over the REAL memory
    # durability backend so an uncommitted stage is genuinely discarded.
    envelope = make_envelope(_DlqEvent('replay'))
    entry = _entry_for(envelope, 'mem://orders')
    sink = _SentSink()
    config = MessagingConfig(
        endpoints=[external_endpoint('mem://orders')],
        outbox=OutboxConfig(),
        dead_letter=DeadLetterConfig(),
        transports={'mem': RecordingTransport},
        observers=[_SentObserver],
    )

    async with create_test_app(
        imports=[MemoryBackend.register(), MessagingModule.register(config)],
        extensions=[MessagingExtension().bind(_DlqEventHandler)],
        providers=[object_(sink, provided_type=_SentSink)],
    ) as app:
        async with app.container() as scope:
            execution = await scope.get(IReplayExecution)
            await execution.dispatch(entry)

        assert sink.destinations == ['mem://orders']  # fired once, only after the durable commit

        async with app.container() as verify_scope:
            outbox = cast('WorkspaceOutboxStore', await verify_scope.get(IOutboxStore))
            committed = list(outbox.messages)

    assert len(committed) == 1  # the restaged row survived request-scope teardown (committed durably)
    assert committed[0].destination == 'mem://orders'


async def test_endpoint_replay_rolls_back_and_fires_no_sent_on_stage_failure() -> None:
    # A failing outbox stage during replay rolls the isolated owner transaction back, re-raises to the
    # caller, and never fires `sent` — no partial delivery, no evidence without a durable commit.
    envelope = make_envelope(_DlqEvent('boom'))
    entry = _entry_for(envelope, 'mem://orders')
    uow = RecordingUoW()
    sink = _SentSink()
    dlq_store = _ReplayStore()
    inbox_store = FakeInboxStore()
    config = MessagingConfig(
        endpoints=[external_endpoint('mem://orders')],
        outbox=OutboxConfig(),
        dead_letter=DeadLetterConfig(),
        transports={'mem': RecordingTransport},
        observers=[_SentObserver],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(_FailingOutboxStore(), provided_type=IOutboxStore),
                object_(inbox_store, provided_type=IInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                object_(sink, provided_type=_SentSink),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        execution = await scope.get(IReplayExecution)
        with pytest.raises(_OutboxUnavailableError):
            await execution.dispatch(entry)

    assert uow.rollback_count == 1  # the isolated owner transaction rolled back (pollers never roll back)
    assert sink.destinations == []  # no evidence without a commit


async def test_endpoint_replay_owner_scope_is_isolated_from_ambient_reprocess_scope() -> None:
    # The outbox restage runs in a FRESH APP-scope child transaction (via AppScopeSource.container),
    # never the ambient reprocess request scope. If the owner regressed to `self._container`, run_committed
    # would re-enter the ambient request container — an ACTION child that SHARES the request-scoped UoW — and
    # eagerly commit the ambient reprocess scope's UoW (commit_count would be 1). A fresh sibling scope leaves
    # the ambient UoW untouched while the owner's own UoW commits the restaged row and fires `sent`. Distinct
    # `scoped(IUnitOfWork, _fresh_uow)` instances per scope make the enlistment observable through DI.
    envelope = make_envelope(_DlqEvent('isolated-replay'))
    entry = _entry_for(envelope, 'mem://orders')
    sink = _SentSink()
    config = MessagingConfig(
        endpoints=[external_endpoint('mem://orders')],
        outbox=OutboxConfig(),
        dead_letter=DeadLetterConfig(),
        transports={'mem': RecordingTransport},
        observers=[_SentObserver],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                scoped(IUnitOfWork, _fresh_uow),
                scoped(IOutboxStore, RecordingOutboxStore),
                scoped(IInboxStore, FakeInboxStore),
                object_(_ReplayStore(), provided_type=IDeadLetterStore),
                object_(sink, provided_type=_SentSink),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        ambient_uow = cast('RecordingUoW', await scope.get(IUnitOfWork))
        execution = await scope.get(IReplayExecution)
        await execution.dispatch(entry)

        assert ambient_uow.commit_count == 0  # owner minted a fresh sibling, never enlisted the ambient scope
        assert ambient_uow.rollback_count == 0
        assert sink.destinations == ['mem://orders']  # owner committed the restage and fired sent post-commit
