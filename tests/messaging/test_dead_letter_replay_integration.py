from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  # Dishka inspects the session factory return annotation
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, TypeGuard
from uuid import uuid4

import anyio
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku._internal.lease import LeaseConfig
from waku._internal.node import NodeId, NodeIdentity
from waku._internal.transaction import TransactionExecutionError
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.memory._internal.transaction import InMemoryCommittedState
from waku.backends.sqlalchemy import (
    SqlAlchemyBackend,
    bind_dead_letter_tables,
    bind_inbox_tables,
    bind_node_tables,
    bind_outbox_tables,
    bind_sequence_tables,
)
from waku.di import object_, scoped, singleton
from waku.messages import IEvent
from waku.messaging import (
    DeliveryOptions,
    EndpointDefaults,
    EventHandler,
    IMessageBus,
    InboxConfig,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    PollingConfig,
    RequestHandler,
    TransactionalBehavior,
)
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import get_message_context
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.endpoints.base import EndpointMode
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayClaim, ReplayClaimOwner
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
    ReplayClaimId,
)
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.outbox import OutboxRelayConfig
from waku.messaging.router import external_endpoint, local_queue, route
from waku.messaging.sequence import GroupId, ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDurabilityStore,
    RecordingUoW,
    StubSubscription,
    make_envelope,
    node_registry_providers,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence
    from uuid import UUID

    from dishka import AsyncContainer
    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku._internal.node import INodeRegistry
    from waku.application import WakuApplication
    from waku.di import Provider
    from waku.messaging.transport.inbound import ConsumeCallback


@dataclass(frozen=True, slots=True)
class _Charge(IRequest[None]):
    amount: int


_attempts: list[int] = []


class _ChargeHandler(RequestHandler[_Charge, None]):
    @override
    async def handle(self, request: _Charge, /) -> None:
        _attempts.append(request.amount)
        if len(_attempts) == 1:
            msg = 'first attempt fails'
            raise RuntimeError(msg)


class _DictDeadLetterStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.rows: dict[UUID, DeadLetterEntry] = {}
        self.claim_owners: list[str] = []

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.rows[entry.id] = entry

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        return self.rows[entry_id]

    @override
    async def mark_replayed(self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        entry = self.rows.get(entry_id)
        if not self._claim_is_live(entry, claim_id, now):
            return False
        self.rows[entry_id] = replace(
            entry,
            status=DeadLetterStatus.REPLAYED,
            replay_owner_id=None,
            replay_lease_expires_at=None,
            replay_claim_id=None,
        )
        return True

    @override
    async def mark_replay_failed(
        self,
        entry_id: UUID,
        error: str,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
    ) -> bool:  # pragma: no cover
        entry = self.rows.get(entry_id)
        if not self._claim_is_live(entry, claim_id, now):
            return False
        self.rows[entry_id] = replace(
            entry,
            status=DeadLetterStatus.REPLAY_FAILED,
            replay_count=entry.replay_count + 1,
            error_message=error,
            replay_owner_id=None,
            replay_lease_expires_at=None,
            replay_claim_id=None,
        )
        return True

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:  # pragma: no cover
        self._validate_expiry(now, lease_expires_at)
        for entry in sorted(self.rows.values(), key=lambda candidate: candidate.created_at or now):
            if entry.status is DeadLetterStatus.REPLAYED:
                continue
            if entry.status is DeadLetterStatus.REPLAY_FAILED and entry.replay_count >= max_replay_count:
                continue
            if not self._claimable(entry, now):
                continue
            claimed = replace(
                entry,
                replay_owner_id=owner_id,
                replay_lease_expires_at=lease_expires_at,
                replay_claim_id=claim_id,
            )
            self.rows[entry.id] = claimed
            return claimed
        return None

    @override
    async def claim_replay(
        self,
        entry_id: UUID,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        self._validate_expiry(now, lease_expires_at)
        self.claim_owners.append(owner_id)
        entry = self.rows.get(entry_id)
        if entry is None or entry.status is DeadLetterStatus.REPLAYED or not self._claimable(entry, now):
            return None
        claimed = replace(
            entry,
            replay_owner_id=owner_id,
            replay_lease_expires_at=lease_expires_at,
            replay_claim_id=claim_id,
        )
        self.rows[entry.id] = claimed
        return claimed

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:  # pragma: no cover
        self._validate_expiry(now, lease_expires_at)
        entry = self.rows.get(entry_id)
        if not self._claim_is_live(entry, claim_id, now):
            return False
        self.rows[entry_id] = replace(entry, replay_lease_expires_at=lease_expires_at)
        return True

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        self.rows.pop(entry_id, None)

    @override
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:  # pragma: no cover
        return 0

    @staticmethod
    def _validate_expiry(now: datetime, lease_expires_at: datetime) -> None:
        if lease_expires_at <= now:
            msg = 'lease_expires_at must be greater than now'
            raise ValueError(msg)

    @staticmethod
    def _claimable(entry: DeadLetterEntry, now: datetime) -> bool:
        return entry.replay_lease_expires_at is None or entry.replay_lease_expires_at <= now

    @staticmethod
    def _claim_is_live(
        entry: DeadLetterEntry | None,
        claim_id: ReplayClaimId,
        now: datetime,
    ) -> TypeGuard[DeadLetterEntry]:
        return (
            entry is not None
            and entry.replay_claim_id == claim_id
            and entry.replay_lease_expires_at is not None
            and entry.replay_lease_expires_at > now
        )


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


def _durability_providers(
    dlq: IDeadLetterStore,
    *,
    outbox: IOutboxStore | None = None,
    inbox: IInboxStore | None = None,
    nodes: INodeRegistry | None = None,
    with_allocator: bool = False,
) -> list[Provider]:
    """Memory-backend durability providers over a shared ``dlq`` store (outbox/inbox default to fresh ones).

    ``with_allocator`` adds the sequence allocator that durable local-queue endpoints require.
    """
    # One registry for the app and its inbox/outbox stores: a real backend keeps membership in the
    # same resource the fence reads, and a split view would let recovery reclaim this node's own rows.
    registry = nodes if nodes is not None else InMemoryNodeRegistry()
    providers = [
        object_(RecordingUoW(), provided_type=IUnitOfWork),
        object_(outbox if outbox is not None else InMemoryOutboxStore(dlq, registry), provided_type=IOutboxStore),
        object_(inbox if inbox is not None else InMemoryInboxStore(dlq, registry), provided_type=IInboxStore),
        object_(dlq, provided_type=IDeadLetterStore),
        scoped(IDurabilityStore, _durability),
        *node_registry_providers(registry),
    ]
    if with_allocator:
        providers.append(object_(RecordingAllocator(), provided_type=ISequenceAllocator))
    return providers


async def test_dead_letter_then_replay_reprocesses_message() -> None:
    _attempts.clear()
    dl_store = _DictDeadLetterStore()
    config = MessagingConfig(
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
        dead_letter=DeadLetterConfig(),
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_ChargeHandler)],
            providers=_durability_providers(dl_store),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.send(_Charge(amount=42))
        await wait_until(lambda: bool(dl_store.rows))

        entry_id = next(iter(dl_store.rows))
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(await dl_store.fetch_one(entry_id)) is True
        await wait_until(lambda: len(_attempts) == 2)

    assert _attempts == [42, 42]
    assert dl_store.rows[entry_id].status is DeadLetterStatus.REPLAYED


async def test_replay_claims_with_the_process_node_identity() -> None:
    _attempts.clear()
    dl_store = _DictDeadLetterStore()
    config = MessagingConfig(
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
        dead_letter=DeadLetterConfig(),
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_ChargeHandler)],
            providers=_durability_providers(dl_store),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.send(_Charge(amount=7))
        await wait_until(lambda: bool(dl_store.rows))

        entry_id = next(iter(dl_store.rows))
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(await dl_store.fetch_one(entry_id)) is True
        identity = await scope.get(NodeIdentity)

    assert dl_store.claim_owners == [identity.node_id]


async def test_manual_replay_does_not_dispatch_while_auto_owner_lease_is_live() -> None:
    _attempts.clear()
    dl_store = _DictDeadLetterStore()
    config = MessagingConfig(
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
        dead_letter=DeadLetterConfig(),
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_ChargeHandler)],
            providers=_durability_providers(dl_store),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.send(_Charge(amount=7))
        await wait_until(lambda: bool(dl_store.rows))
        entry = next(iter(dl_store.rows.values()))
        now = datetime.now(tz=UTC)
        claimed = await dl_store.claim_replay(
            entry.id,
            owner_id=NodeId('auto-owner'),
            claim_id=ReplayClaimId(uuid4()),
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
        assert claimed is not None

        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(entry) is False

    assert _attempts == [7]


@dataclass(frozen=True, slots=True)
class _ReplayLivenessProbe(IEvent):
    value: str


class _StepClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 16, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self._now
        self._now += timedelta(milliseconds=1)
        return current

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class _MemoryReplayTrace:
    def __init__(self) -> None:
        self.handler_started = anyio.Event()
        self.release_handler = anyio.Event()
        self.reset()

    def reset(self) -> None:
        self.begin_attempted = anyio.Event()
        self.begin_acquired = anyio.Event()
        self.begin_attempts = 0
        self.begin_acquisitions = 0
        self.allocated: int | None = None


class _TracingInMemoryCommittedState(InMemoryCommittedState):
    def __init__(self) -> None:
        super().__init__()
        self.trace = _MemoryReplayTrace()

    @override
    async def begin(self, borrower: object) -> Any:
        self.trace.begin_attempts += 1
        self.trace.begin_attempted.set()
        staged = await super().begin(borrower)
        self.trace.begin_acquisitions += 1
        self.trace.begin_acquired.set()
        return staged


class _MemoryReplayLivenessHandler(EventHandler[_ReplayLivenessProbe]):
    def __init__(
        self,
        committed: InMemoryCommittedState,
        allocator: ISequenceAllocator,
    ) -> None:
        if not isinstance(committed, _TracingInMemoryCommittedState):
            msg = 'Memory liveness test requires the tracing committed state'
            raise TypeError(msg)
        self._trace = committed.trace
        self._allocator = allocator

    @override
    async def handle(self, message: _ReplayLivenessProbe, /) -> None:
        self._trace.allocated = await self._allocator.allocate(GroupId(message.value))
        self._trace.handler_started.set()
        await self._trace.release_handler.wait()


class _MemoryReplayLivenessHarness:
    def __init__(self) -> None:
        self.clock = _StepClock()
        self.lease = LeaseConfig(ttl_seconds=0.09)
        self.dead_letter = DeadLetterConfig(replay_lease=self.lease)
        self.config = MessagingConfig(
            dead_letter=self.dead_letter,
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        self.group_id = GroupId('memory-replay-liveness')

    @asynccontextmanager
    async def application(self) -> AsyncGenerator[WakuApplication]:
        async with create_test_app(
            base=MemoryBackend.register(),
            imports=[MessagingModule.register(self.config)],
            extensions=[MessagingExtension().bind(_MemoryReplayLivenessHandler)],
            providers=[singleton(InMemoryCommittedState, _TracingInMemoryCommittedState)],
        ) as app:
            yield app


class _SqlReplayTrace:
    def __init__(self) -> None:
        self.handler_session: AsyncSession | None = None
        self.renewal_session: AsyncSession | None = None
        self.commits: list[AsyncSession] = []
        self.closed: list[AsyncSession] = []
        self.claim_closed_before_handler = False
        self.renewal_commit_started = anyio.Event()
        self.allow_renewal_commit = anyio.Event()
        self.renewal_committed = anyio.Event()

    def reset_after_seed(self) -> None:
        self.commits.clear()
        self.closed.clear()


class _TracingAsyncSession(AsyncSession):
    def __init__(self, bind: AsyncEngine, trace: _SqlReplayTrace) -> None:
        super().__init__(bind, expire_on_commit=False)
        self._trace = trace

    @override
    async def commit(self) -> None:
        handler_session = self._trace.handler_session
        is_renewal = handler_session is not None and self is not handler_session and handler_session.in_transaction()
        if is_renewal:
            self._trace.renewal_session = self
            self._trace.renewal_commit_started.set()
            await self._trace.allow_renewal_commit.wait()
        await super().commit()
        self._trace.commits.append(self)
        if is_renewal:
            self._trace.renewal_committed.set()


class _SqlReplayControl:
    def __init__(self, trace: _SqlReplayTrace) -> None:
        self.trace = trace
        self.handler_started = anyio.Event()
        self.release_handler = anyio.Event()
        self.allocated: int | None = None


class _SqlReplayLivenessHandler(EventHandler[_ReplayLivenessProbe]):
    def __init__(
        self,
        control: _SqlReplayControl,
        session: AsyncSession,
        allocator: ISequenceAllocator,
    ) -> None:
        self._control = control
        self._session = session
        self._allocator = allocator

    @override
    async def handle(self, message: _ReplayLivenessProbe, /) -> None:
        self._control.allocated = await self._allocator.allocate(GroupId(message.value))
        trace = self._control.trace
        trace.handler_session = self._session
        trace.claim_closed_before_handler = len(trace.commits) == 1 and trace.commits[0] in trace.closed
        self._control.handler_started.set()
        await self._control.release_handler.wait()


class _SqlReplayLivenessHarness:
    def __init__(self, pg_engine: AsyncEngine) -> None:
        self._pg_engine = pg_engine
        self.metadata = MetaData()
        bind_outbox_tables(self.metadata)
        bind_inbox_tables(self.metadata)
        bind_dead_letter_tables(self.metadata)
        bind_sequence_tables(self.metadata)
        # Membership is part of the durability schema now: the app registers this node while it boots.
        bind_node_tables(self.metadata)
        self.trace = _SqlReplayTrace()
        self.control = _SqlReplayControl(self.trace)
        self.clock = _StepClock()
        self.dead_letter = DeadLetterConfig(replay_lease=LeaseConfig(ttl_seconds=0.3))
        self.config = MessagingConfig(
            dead_letter=self.dead_letter,
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        self.group_id = GroupId('sql-replay-liveness')

    async def session_factory(self) -> AsyncIterator[AsyncSession]:
        session = _TracingAsyncSession(self._pg_engine, self.trace)
        try:
            yield session
        finally:
            await session.close()
            self.trace.closed.append(session)

    @asynccontextmanager
    async def application(self) -> AsyncGenerator[WakuApplication]:
        async with self._pg_engine.begin() as connection:
            await connection.run_sync(self.metadata.create_all)
        try:
            async with create_test_app(
                imports=[
                    MessagingModule.register(self.config),
                    SqlAlchemyBackend.register(session_factory=self.session_factory, metadata=self.metadata),
                ],
                extensions=[MessagingExtension().bind(_SqlReplayLivenessHandler)],
                providers=[object_(self.control, provided_type=_SqlReplayControl)],
            ) as app:
                yield app
        finally:
            async with self._pg_engine.begin() as connection:
                await connection.run_sync(self.metadata.drop_all)


@dataclass(frozen=True, slots=True)
class _MemoryReplayEvidence:
    result: tuple[bool, ...]
    errors: tuple[BaseException, ...]
    contender: ReplayClaim | None
    final_entry: DeadLetterEntry
    stale_claim_finalized: bool | None
    next_sequence: int


@dataclass(frozen=True, slots=True)
class _MemoryReplayCase:
    entry: DeadLetterEntry
    claimed: ReplayClaim
    execution: IReplayExecution
    owner: ReplayClaimOwner
    contender: ReplayClaimOwner
    trace: _MemoryReplayTrace


@dataclass(frozen=True, slots=True)
class _SqlReplayCase:
    claimed: ReplayClaim
    execution: IReplayExecution
    owner: ReplayClaimOwner
    initial_expiry: datetime | None


def _liveness_entry(
    codec: PayloadCodec,
    handler: type[EventHandler[_ReplayLivenessProbe]],
    value: str,
) -> DeadLetterEntry:
    envelope = make_envelope(_ReplayLivenessProbe(value))
    return DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination=handler_destination(handler),
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='replay probe',
        retry_count=0,
        message_id=envelope.message_id,
        metadata=encode_metadata(envelope),
    )


async def _commit_dead_letter(container: AsyncContainer, entry: DeadLetterEntry) -> None:
    async with container() as scope:
        await (await scope.get(IDeadLetterStore)).save(entry)
        await (await scope.get(IUnitOfWork)).commit()


async def _fetch_dead_letter(container: AsyncContainer, entry_id: UUID) -> DeadLetterEntry:
    async with container() as scope:
        store: IDeadLetterStore = await scope.get(IDeadLetterStore)
        return await store.fetch_one(entry_id)


async def _commit_sequence(container: AsyncContainer, group_id: GroupId) -> int:
    async with container() as scope:
        allocator: ISequenceAllocator = await scope.get(ISequenceAllocator)
        value = await allocator.allocate(group_id)
        await (await scope.get(IUnitOfWork)).commit()
        return value


async def _prepare_sql_replay(
    container: AsyncContainer,
    scope: AsyncContainer,
    codec: PayloadCodec,
    harness: _SqlReplayLivenessHarness,
) -> _SqlReplayCase:
    entry = _liveness_entry(codec, _SqlReplayLivenessHandler, str(harness.group_id))
    await _commit_dead_letter(container, entry)
    harness.trace.reset_after_seed()
    execution = await scope.get(IReplayExecution)
    owner = ReplayClaimOwner(
        container=container,
        config=harness.dead_letter,
        node_id=NodeId('replay-node-a'),
        now=harness.clock,
    )
    claimed = await owner.claim_replayable()
    assert claimed is not None
    return _SqlReplayCase(claimed, execution, owner, claimed.entry.replay_lease_expires_at)


async def _prepare_memory_replay(
    container: AsyncContainer,
    scope: AsyncContainer,
    codec: PayloadCodec,
    harness: _MemoryReplayLivenessHarness,
) -> _MemoryReplayCase:
    entry = _liveness_entry(codec, _MemoryReplayLivenessHandler, str(harness.group_id))
    await _commit_dead_letter(container, entry)
    execution = await scope.get(IReplayExecution)
    committed = await scope.get(InMemoryCommittedState)
    assert isinstance(committed, _TracingInMemoryCommittedState)
    # Two distinct nodes: under D1 a replay contender is another NODE, so it carries another NodeId.
    owner = ReplayClaimOwner(
        container=container,
        config=harness.dead_letter,
        node_id=NodeId('replay-node-a'),
        now=harness.clock,
    )
    contender = ReplayClaimOwner(
        container=container,
        config=harness.dead_letter,
        node_id=NodeId('replay-node-b'),
        now=harness.clock,
    )
    claimed = await owner.claim_replayable()
    assert claimed is not None
    committed.trace.reset()
    return _MemoryReplayCase(entry, claimed, execution, owner, contender, committed.trace)


async def _exercise_memory_replay_liveness(*, overrun: bool) -> _MemoryReplayEvidence:
    harness = _MemoryReplayLivenessHarness()
    async with harness.application() as app, app.container() as scope:
        replay_case = await _prepare_memory_replay(app.container, scope, await scope.get(PayloadCodec), harness)
        results: list[bool] = []
        errors: list[BaseException] = []
        contender_results: list[ReplayClaim | None] = []

        async def replay() -> None:
            try:
                results.append(await replay_case.owner.replay_claimed(replay_case.claimed, replay_case.execution))
            except BaseException as error:  # noqa: BLE001 -- overrun deliberately captures lost-owner evidence
                errors.append(error)

        async def contend() -> None:
            contender_results.append(await replay_case.contender.claim_replay(replay_case.entry.id))

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(replay)
            await replay_case.trace.handler_started.wait()
            await replay_case.trace.begin_acquired.wait()
            assert replay_case.trace.begin_acquisitions == 1
            await wait_until(lambda: replay_case.trace.begin_attempts == 2)
            assert replay_case.trace.begin_acquisitions == 1
            if overrun:
                harness.clock.advance(timedelta(seconds=harness.lease.ttl_seconds * 2))
            task_group.start_soon(contend)
            await wait_until(lambda: replay_case.trace.begin_attempts == 3)
            assert replay_case.trace.begin_acquisitions == 1
            replay_case.trace.release_handler.set()

        stale_claim_finalized: bool | None = None
        if overrun:
            async with app.container() as finalize_scope:
                store = await finalize_scope.get(IDeadLetterStore)
                stale_claim_finalized = await store.mark_replayed(
                    replay_case.entry.id,
                    claim_id=replay_case.claimed.claim_id,
                    now=harness.clock(),
                )
                await (await finalize_scope.get(IUnitOfWork)).commit()
        final_entry = await _fetch_dead_letter(app.container, replay_case.entry.id)
        next_sequence = await _commit_sequence(app.container, harness.group_id)

    return _MemoryReplayEvidence(
        result=tuple(results),
        errors=tuple(errors),
        contender=contender_results[0],
        final_entry=final_entry,
        stale_claim_finalized=stale_claim_finalized,
        next_sequence=next_sequence,
    )


async def test_memory_backend_below_ttl_serializes_renewal_contender_and_finalization() -> None:
    evidence = await _exercise_memory_replay_liveness(overrun=False)

    assert evidence.result == (True,)
    assert evidence.errors == ()
    assert evidence.contender is None
    assert evidence.final_entry.status is DeadLetterStatus.REPLAYED
    assert evidence.final_entry.replay_owner_id is None
    assert evidence.final_entry.replay_count == 0
    assert evidence.stale_claim_finalized is None
    assert evidence.next_sequence == 2


async def test_memory_backend_ttl_overrun_commits_handler_but_rejects_stale_claim() -> None:
    evidence = await _exercise_memory_replay_liveness(overrun=True)

    assert evidence.result == ()
    assert len(evidence.errors) == 1
    error = evidence.errors[0]
    assert isinstance(error, TransactionExecutionError)
    assert 'Replay claim ownership was lost' in str(error.error)
    assert evidence.contender is not None
    assert evidence.final_entry.status is DeadLetterStatus.PENDING
    assert evidence.final_entry.replay_owner_id == evidence.contender.entry.replay_owner_id
    assert evidence.final_entry.replay_claim_id == evidence.contender.claim_id
    assert evidence.final_entry.replay_count == 0
    assert evidence.stale_claim_finalized is False
    assert evidence.next_sequence == 2


async def test_sqlalchemy_renews_in_distinct_transaction_while_handler_transaction_is_open(
    pg_engine: AsyncEngine,
) -> None:
    harness = _SqlReplayLivenessHarness(pg_engine)
    async with harness.application() as app, app.container() as scope:
        replay = await _prepare_sql_replay(app.container, scope, await scope.get(PayloadCodec), harness)
        results: list[bool] = []

        async def run_replay() -> None:
            results.append(await replay.owner.replay_claimed(replay.claimed, replay.execution))

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_replay)
            await harness.control.handler_started.wait()
            await harness.trace.renewal_commit_started.wait()
            assert harness.trace.claim_closed_before_handler is True
            assert harness.trace.handler_session is not None
            assert harness.trace.handler_session.in_transaction()
            assert harness.trace.renewal_session is not None
            assert harness.trace.renewal_session is not harness.trace.handler_session
            before_renewal_commit = await _fetch_dead_letter(app.container, replay.claimed.entry.id)
            assert before_renewal_commit.replay_owner_id == replay.owner.owner_id
            assert before_renewal_commit.replay_lease_expires_at == replay.initial_expiry
            harness.trace.allow_renewal_commit.set()
            await harness.trace.renewal_committed.wait()
            renewed = await _fetch_dead_letter(app.container, replay.claimed.entry.id)
            assert renewed.replay_owner_id == replay.owner.owner_id
            assert renewed.replay_lease_expires_at is not None
            assert replay.initial_expiry is not None
            assert renewed.replay_lease_expires_at > replay.initial_expiry
            assert harness.trace.handler_session.in_transaction()
            harness.control.release_handler.set()

        assert results == [True]
        assert harness.control.allocated == 1
        assert len(harness.trace.commits) == 4
        assert harness.trace.commits[0] in harness.trace.closed
        assert harness.trace.commits[1] is harness.trace.renewal_session
        assert harness.trace.commits[2] is harness.trace.handler_session
        assert len({id(session) for session in harness.trace.commits}) == 4
        finalized = await _fetch_dead_letter(app.container, replay.claimed.entry.id)
        assert finalized.status is DeadLetterStatus.REPLAYED
        assert finalized.replay_owner_id is None
        assert finalized.replay_claim_id is None
        assert finalized.replay_count == 0
        assert await _commit_sequence(app.container, harness.group_id) == 2


_FAST_POLLING = PollingConfig(
    poll_interval_min_seconds=0.01,
    poll_interval_max_seconds=0.05,
    poll_interval_step_seconds=0.01,
)


class _FlakyTransport(ITransport):
    """Fails every send until ``working`` is flipped, then records sends."""

    def __init__(self) -> None:
        self.working = False
        self.sent: list[tuple[dict[str, Any], str, EnvelopeMetadata]] = []

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        if not self.working:
            msg = 'transport down'
            raise ConnectionError(msg)
        self.sent.append((body, destination, metadata))

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        return StubSubscription()

    @override
    async def start(self) -> None: ...
    @override
    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _OrderAuditHandler(EventHandler[_OrderPlaced]):
    """Inert subscriber: route() validation requires a registered handler for the routed type."""

    @override
    async def handle(self, message: _OrderPlaced, /) -> None: ...


class _FlakyOrderHandler(EventHandler[_OrderPlaced]):
    broken: ClassVar[bool] = True
    attempts: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        type(self).attempts.append(message.order_id)
        if type(self).broken:
            msg = 'handler down'
            raise RuntimeError(msg)


class _AlwaysFailingHandler(EventHandler[_OrderPlaced]):
    attempts: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        type(self).attempts.append(message.order_id)
        msg = 'still failing on replay'
        raise RuntimeError(msg)


class _TenantRecordingHandler(EventHandler[_OrderPlaced]):
    broken: ClassVar[bool] = True
    seen_tenants: ClassVar[list[str | None]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        type(self).seen_tenants.append(get_message_context().tenant_id)
        if type(self).broken:
            msg = 'handler down'
            raise RuntimeError(msg)


async def test_tenant_id_survives_outbox_dead_letter_replay_via_metadata_blob() -> None:
    # Blob round-trip, outbox side: publish(tenant) -> outbox persist writes tenant_id into the metadata
    # JSONB blob -> relay exhaustion copies the blob verbatim into the DLQ entry -> replay reads it back
    # (wire_metadata_from_entry) and the transport receives EnvelopeMetadata with the original tenant.
    transport = _FlakyTransport()
    dlq = InMemoryDeadLetterStore()
    registry = InMemoryNodeRegistry()
    outbox = InMemoryOutboxStore(dlq, registry)
    config = MessagingConfig(
        endpoints=[external_endpoint('flaky://orders')],
        routing=[route(_OrderPlaced).to('flaky://orders')],
        outbox=OutboxConfig(
            relay=OutboxRelayConfig(polling=_FAST_POLLING, recovery_interval=timedelta(hours=1), max_attempts=1),
        ),
        dead_letter=DeadLetterConfig(),
        transports={'flaky': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_OrderAuditHandler)],
            providers=_durability_providers(dlq, outbox=outbox, nodes=registry),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.publish(_OrderPlaced(order_id='o-tenant'), DeliveryOptions(tenant_id='t-acme'))
        await wait_until(lambda: bool(dlq.entries))

        entry = next(iter(dlq.entries.values()))
        assert entry.metadata is not None
        assert entry.metadata['tenant_id'] == 't-acme'  # outbox blob copied verbatim into the DLQ

        transport.working = True
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(entry) is True
        await wait_until(lambda: bool(transport.sent))

    _, _, metadata = transport.sent[0]
    assert metadata.tenant_id == 't-acme'  # read back from the blob on replay


async def test_tenant_id_survives_inbox_dead_letter_replay_to_handler_context() -> None:
    # Blob round-trip, inbox side: publish(tenant) -> durable local queue persists tenant_id into the
    # inbox metadata blob -> drainer rebuilds the envelope (handler sees the tenant in its context) ->
    # poison moves the blob into the DLQ -> replay rebuilds again and the handler still sees the tenant.
    _TenantRecordingHandler.broken = True
    _TenantRecordingHandler.seen_tenants = []
    dlq = InMemoryDeadLetterStore()
    registry = InMemoryNodeRegistry()
    inbox = InMemoryInboxStore(dlq, registry)
    config = MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(),
        dead_letter=DeadLetterConfig(),
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().requeue(max_attempts=1),)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_TenantRecordingHandler)],
            providers=_durability_providers(dlq, inbox=inbox, nodes=registry, with_allocator=True),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.publish(_OrderPlaced(order_id='o-tenant'), DeliveryOptions(tenant_id='t-acme'))
        await wait_until(lambda: bool(dlq.entries))

        entry = next(iter(dlq.entries.values()))
        assert entry.metadata is not None
        assert entry.metadata['tenant_id'] == 't-acme'  # inbox blob carried into the DLQ

        _TenantRecordingHandler.broken = False
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(entry) is True

    # Original inbox-drain attempt + replay attempt both observed the tenant from the blob.
    assert _TenantRecordingHandler.seen_tenants == ['t-acme', 't-acme']
    assert dlq.entries[entry.id].status is DeadLetterStatus.REPLAYED


class _ScopedRecordingUoW(IUnitOfWork):
    """Param-less scoped UoW: one instance per DI scope, all instances observable via the ClassVar."""

    instances: ClassVar[list[_ScopedRecordingUoW]] = []

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        type(self).instances.append(self)

    @override
    async def commit(self) -> None:
        self.commit_count += 1

    @override
    async def rollback(self) -> None:
        self.rollback_count += 1


async def test_outbox_exhaustion_dead_letter_replays() -> None:
    # Path 2 end-to-end (memory backend): relay exhaustion -> move_to_dead_letter persists the FULL
    # wire fields into the SHARED DLQ (B-9/B-28 fixed) and frees the outbox idempotency pair; replay
    # re-dispatches through the router and the message finally reaches the transport.
    transport = _FlakyTransport()
    dlq = InMemoryDeadLetterStore()
    registry = InMemoryNodeRegistry()
    outbox = InMemoryOutboxStore(dlq, registry)
    config = MessagingConfig(
        endpoints=[external_endpoint('flaky://orders')],
        routing=[route(_OrderPlaced).to('flaky://orders')],
        outbox=OutboxConfig(
            relay=OutboxRelayConfig(polling=_FAST_POLLING, recovery_interval=timedelta(hours=1), max_attempts=1),
        ),
        dead_letter=DeadLetterConfig(),
        transports={'flaky': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_OrderAuditHandler)],
            providers=_durability_providers(dlq, outbox=outbox, nodes=registry),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.publish(_OrderPlaced(order_id='o-replay'))
        await wait_until(lambda: bool(dlq.entries))

        entry = next(iter(dlq.entries.values()))
        assert entry.destination_kind is DeadLetterDestinationKind.ENDPOINT
        assert entry.destination == 'flaky://orders'
        assert entry.metadata is not None  # the wire fields survived move_to_dead_letter

        transport.working = True
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(entry) is True
        await wait_until(lambda: bool(transport.sent))

    assert dlq.entries[entry.id].status is DeadLetterStatus.REPLAYED
    body, destination, metadata = transport.sent[0]
    assert body == {'order_id': 'o-replay'}
    assert destination == 'orders'
    assert metadata.message_id == str(entry.message_id)  # original identity preserved through the DLQ


async def test_inbox_poison_dead_letter_replays() -> None:
    # Path 4 end-to-end (memory backend): a durable-inbox handler exhausts its requeue budget -> the
    # HANDLER-kind dead letter lands in the SHARED DLQ (B-29 fixed); after the handler is healthy the
    # replay reprocesses that ONE handler inline (B-10 fixed) and marks the entry REPLAYED.
    _FlakyOrderHandler.broken = True
    _FlakyOrderHandler.attempts = []
    dlq = InMemoryDeadLetterStore()
    registry = InMemoryNodeRegistry()
    inbox = InMemoryInboxStore(dlq, registry)
    config = MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(),
        dead_letter=DeadLetterConfig(),
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().requeue(max_attempts=1),)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FlakyOrderHandler)],
            providers=_durability_providers(dlq, inbox=inbox, nodes=registry, with_allocator=True),
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.publish(_OrderPlaced(order_id='o-poison'))
        await wait_until(lambda: bool(dlq.entries))

        entry = next(iter(dlq.entries.values()))
        assert entry.destination_kind is DeadLetterDestinationKind.HANDLER
        assert entry.destination == handler_destination(_FlakyOrderHandler)
        assert inbox.entries == {}  # the poison row was moved out of the inbox

        _FlakyOrderHandler.broken = False
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay(entry) is True

    assert _FlakyOrderHandler.attempts == ['o-poison', 'o-poison']  # original failure + replay success
    assert dlq.entries[entry.id].status is DeadLetterStatus.REPLAYED


async def test_replay_of_handler_entry_does_not_commit_worker_claim_tx() -> None:
    # PIN-F guard: the HANDLER reprocess runs in a FRESH request scope whose transaction is rolled
    # back on re-failure, while every worker claim/mark scope commits exactly once. A re-failure
    # marks REPLAY_FAILED — never a second dead letter.
    _AlwaysFailingHandler.attempts = []
    _ScopedRecordingUoW.instances = []
    dlq = InMemoryDeadLetterStore()
    envelope = make_envelope(_OrderPlaced(order_id='o-fail'))
    config = MessagingConfig(
        dead_letter=DeadLetterConfig(
            auto_replay_enabled=True,
            max_replay_count=1,
            polling=_FAST_POLLING,
            stop_timeout=timedelta(seconds=1),
        ),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    registry = InMemoryNodeRegistry()
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
            providers=[
                scoped(IUnitOfWork, _ScopedRecordingUoW),
                object_(InMemoryOutboxStore(dlq, registry), provided_type=IOutboxStore),
                object_(InMemoryInboxStore(dlq, registry), provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
                *node_registry_providers(registry),
            ],
        ) as app,
        app.container() as scope,
    ):
        payload_codec = await scope.get(PayloadCodec)
        entry = DeadLetterEntry(
            id=uuid4(),
            message_type=envelope.message_type,
            payload=encode_payload(envelope, payload_codec),
            destination=handler_destination(_AlwaysFailingHandler),
            destination_kind=DeadLetterDestinationKind.HANDLER,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            error_type='RuntimeError',
            error_message='boom',
            retry_count=3,
            message_id=envelope.message_id,
            metadata=encode_metadata(envelope),
        )
        await dlq.save(entry)
        await wait_until(lambda: dlq.entries[entry.id].status is DeadLetterStatus.REPLAY_FAILED)

    assert _AlwaysFailingHandler.attempts == ['o-fail']  # max_replay_count=1: reprocessed exactly once
    assert list(dlq.entries) == [entry.id]  # re-failure marks REPLAY_FAILED; NO second dead letter
    assert dlq.entries[entry.id].replay_count == 1

    # PIN-F: the failed reprocess transaction is its OWN scope's UoW — rolled back, never committed —
    # and it is a different instance from every worker claim/mark scope UoW (each commits exactly once).
    rolled_back = [uow for uow in _ScopedRecordingUoW.instances if uow.rollback_count]
    assert len(rolled_back) == 1
    assert rolled_back[0].commit_count == 0
    worker_scopes = [uow for uow in _ScopedRecordingUoW.instances if uow.commit_count]
    assert worker_scopes  # the claim/mark scope exists and is distinct from the reprocess scope
    assert all(uow.commit_count == 1 for uow in worker_scopes)
    validation_scopes = [
        uow for uow in _ScopedRecordingUoW.instances if not uow.commit_count and not uow.rollback_count
    ]
    assert len(validation_scopes) == 1  # same-child capability validation is intentionally side-effect free
