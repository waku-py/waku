from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import anyio
from dishka import Provider, Scope, provide
from typing_extensions import override

from waku._internal.node import INodeRegistry, NodeId, NodeRegistryConfig
from waku._internal.retort import default_retort
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.di import (
    Provider as WakuProvider,
    object_,
    scoped,
)
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.durability import (
    DefaultDurabilityStore,
    IDeadLetterStore,
    IDurabilityStore,
    IInboxStore,
    IOutboxStore,
)
from waku.messaging.endpoints._internal.execution import IEndpointExecution, noop_result_observer
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, ReplayClaimId
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.inbox import EndpointUri, InboxStatus
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.outbox.relay import OutboxRelayConfig, build_relay_default_policy
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicyRegistry
from waku.messaging.sequence import GroupId, ISequenceAllocator
from waku.messaging.transport._internal.registry import TransportRegistry
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Protocol
    from uuid import UUID

    from waku.messages import IMessage
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.execution import ExecutionResult, ResultObserver, TerminalIntent
    from waku.messaging.endpoints.outcome import ExecutionOutcome
    from waku.messaging.sending import SendingFailurePolicy
    from waku.messaging.transport.inbound import ConsumeCallback

    class _OrderMessage(Protocol):
        order_id: str


def make_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


def make_envelope(
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    group_id: str | None = None,
    tenant_id: str | None = None,
    scheduled_time: datetime | None = None,
    expires_at: datetime | None = None,
) -> MessageEnvelope[Any]:
    payload_type = type(payload)
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        message_type=f'{payload_type.__module__}.{payload_type.__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=payload,
        headers=headers or {},
        group_id=group_id,
        tenant_id=tenant_id,
        scheduled_time=scheduled_time,
        expires_at=expires_at,
    )


def make_relay_evaluator(
    config: OutboxRelayConfig,
    *,
    destination_policies: Mapping[str, Sequence[SendingFailurePolicy]] | None = None,
) -> SendingFailureEvaluator:
    """Build the relay's `SendingFailureEvaluator` with the synthesized default + optional per-destination policies."""
    registry = SendingFailurePolicyRegistry(
        destination_policies=destination_policies or {},
        default_policies=(build_relay_default_policy(config),),
    )
    return SendingFailureEvaluator(registry=registry)


def make_inbox_entry(
    envelope: MessageEnvelope[Any],
    handler_type: HandlerType,
    *,
    codec: PayloadCodec,
    status: InboxStatus = InboxStatus.INCOMING,
    execution_time: datetime | None = None,
) -> InboxEntry:
    return InboxEntry(
        id=envelope.message_id,
        payload=encode_payload(envelope, codec),
        message_type=envelope.message_type,
        source_uri=EndpointUri('orders'),
        destination=handler_destination(handler_type),
        owner_id=None,
        status=status,
        execution_time=execution_time,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        metadata=encode_metadata(envelope),
    )


NOOP_EVALUATOR = ErrorPolicyEvaluator(registry=ErrorPolicyRegistry(handler_policies={}, default_policies=()))
NOOP_OBSERVERS = MessageObservers([])


class StubEndpointExecution(IEndpointExecution):
    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        await on_result(result.outcome, intent.error)


class EndpointSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


class EndpointOnlyObserver(IMessageObserver):
    def __init__(self, sink: EndpointSink) -> None:
        self._sink = sink

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self._sink.events.append(('executing', destination))

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._sink.events.append(('executed', destination))


class RecordingUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.committed = False
        self.rolled_back = False
        self.commit_count = 0
        self.rollback_count = 0
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    @override
    async def commit(self) -> None:
        if self._commit_error:
            raise self._commit_error
        self.committed = True
        self.commit_count += 1

    @override
    async def rollback(self) -> None:
        if self._rollback_error:
            raise self._rollback_error
        self.rolled_back = True
        self.rollback_count += 1


class RecordingDurabilityStore(IDurabilityStore):
    """Coherent test capability whose facets are the exact objects supplied by a fixture."""

    def __init__(
        self,
        *,
        unit_of_work: IUnitOfWork,
        outbox: IOutboxStore,
        inbox: IInboxStore,
        dead_letters: IDeadLetterStore,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._outbox = outbox
        self._inbox = inbox
        self._dead_letters = dead_letters

    @property
    @override
    def unit_of_work(self) -> IUnitOfWork:
        return self._unit_of_work

    @property
    @override
    def outbox(self) -> IOutboxStore:
        return self._outbox

    @property
    @override
    def inbox(self) -> IInboxStore:
        return self._inbox

    @property
    @override
    def dead_letters(self) -> IDeadLetterStore:
        return self._dead_letters


def durability_for_inbox(unit_of_work: IUnitOfWork, inbox: IInboxStore) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=inbox,
        dead_letters=RecordingDeadLetterStore(),
    )


def durability_for_outbox(unit_of_work: IUnitOfWork, outbox: IOutboxStore) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=FakeInboxStore(),
        dead_letters=RecordingDeadLetterStore(),
    )


def durability_for_outbox_and_inbox(
    unit_of_work: IUnitOfWork,
    outbox: IOutboxStore,
    inbox: IInboxStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=inbox,
        dead_letters=RecordingDeadLetterStore(),
    )


def durability_for_inbox_and_dead_letters(
    unit_of_work: IUnitOfWork,
    inbox: IInboxStore,
    dead_letters: IDeadLetterStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=inbox,
        dead_letters=dead_letters,
    )


def durability_for_outbox_and_dead_letters(
    unit_of_work: IUnitOfWork,
    outbox: IOutboxStore,
    dead_letters: IDeadLetterStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=FakeInboxStore(),
        dead_letters=dead_letters,
    )


def durability_for_dead_letters(
    unit_of_work: IUnitOfWork,
    dead_letters: IDeadLetterStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=FakeInboxStore(),
        dead_letters=dead_letters,
    )


def node_registry_providers(registry: INodeRegistry | None = None) -> list[WakuProvider]:
    """Membership pair every durability-configured app must publish, as the wiring gate requires.

    One shared registry object rather than a scoped one: real backends keep membership in committed
    rows, so it must outlive the request scope that wrote it. Pass ``registry`` (typically
    ``FakeInboxStore.nodes``) when the app's inbox store must resolve ownership against the SAME
    membership the app registers itself into; a real backend keeps both in one database.
    """
    return [
        object_(registry if registry is not None else InMemoryNodeRegistry(), provided_type=INodeRegistry),
        object_(NodeRegistryConfig(), provided_type=NodeRegistryConfig),
    ]


def durability_providers(
    inbox: IInboxStore | None = None,
    *,
    with_node_registry: bool = True,
    extra: Sequence[Provider] = (),
) -> list[Provider]:
    """In-memory durability provider set for messaging integration apps.

    A shared ``inbox`` instance is supplied via ``object_`` when given; otherwise a fresh
    ``FakeInboxStore`` is request-scoped. The node registry is shared as one object so membership
    survives across request scopes the way a real backend's rows do, and it is the SAME registry the
    supplied inbox fences against — a split view would let recovery reclaim this node's own rows.
    ``with_node_registry=False`` reproduces the misassembly the wiring gate rejects. ``extra`` appends
    call-site-specific providers.
    """
    inbox_provider = (
        object_(inbox, provided_type=IInboxStore) if inbox is not None else scoped(IInboxStore, FakeInboxStore)
    )
    shared_registry = inbox.nodes if isinstance(inbox, FakeInboxStore) else None
    registry_providers = node_registry_providers(shared_registry) if with_node_registry else []
    return [
        object_(RecordingUoW(), provided_type=IUnitOfWork),
        inbox_provider,
        object_(RecordingAllocator(), provided_type=ISequenceAllocator),
        scoped(IDeadLetterStore, InMemoryDeadLetterStore),
        scoped(IOutboxStore, InMemoryOutboxStore),
        scoped(IDurabilityStore, DefaultDurabilityStore),
        *registry_providers,
        *extra,
    ]


class RecordingDeadLetterStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.entries: list[DeadLetterEntry] = []

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.entries.append(entry)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:  # pragma: no cover
        raise KeyError(entry_id)

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

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
    ) -> DeadLetterEntry | None:  # pragma: no cover
        return None

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def mark_replayed(
        self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def mark_replay_failed(
        self, entry_id: UUID, error: str, *, claim_id: ReplayClaimId, now: datetime
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:  # pragma: no cover
        return 0


class FailingDeadLetterStore(IDeadLetterStore):
    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        msg = 'DLQ store unavailable'
        raise ConnectionError(msg)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:  # pragma: no cover
        raise KeyError(entry_id)

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

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
    ) -> DeadLetterEntry | None:  # pragma: no cover
        return None

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def mark_replayed(
        self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def mark_replay_failed(
        self, entry_id: UUID, error: str, *, claim_id: ReplayClaimId, now: datetime
    ) -> bool:  # pragma: no cover
        return False

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:  # pragma: no cover
        return 0


class StubSubscription(Subscription):
    """No-op Subscription double for fake transports whose ``subscribe`` is never driven."""

    @override
    async def pause(self) -> None: ...

    @override
    async def resume(self) -> None: ...


class RecordingTransport(ITransport):
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], str, EnvelopeMetadata, IEnvelopeMapper[Any, Any] | None]] = []
        self.sent_event = anyio.Event()
        self.subscribed: list[tuple[str, ConsumeCallback, IEnvelopeMapper[Any, Any] | None]] = []

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        self.sent.append((body, destination, metadata, mapper))
        self.sent_event.set()

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        self.subscribed.append((queue, on_message, mapper))
        return StubSubscription()

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


class RecordingAllocator(ISequenceAllocator):
    """ISequenceAllocator double: records group_ids in ``calls`` and returns per-group sequences."""

    def __init__(self) -> None:
        self.calls: list[GroupId] = []
        self._counters: dict[GroupId, int] = {}

    @override
    async def allocate(self, group_id: GroupId) -> int:
        self.calls.append(group_id)
        self._counters[group_id] = self._counters.get(group_id, 0) + 1
        return self._counters[group_id]


def order_id_partition(msg: IMessage) -> str | None:
    """partition_by extractor for test messages carrying an ``order_id`` attribute."""
    return cast('_OrderMessage', msg).order_id


class EndpointDepsProvider(Provider):
    """REQUEST-scope dependency set for durable inbox/queue endpoint tests.

    Provides the inbox and dead-letter stores plus a UoW, sequence allocator, and codec.
    """

    scope = Scope.REQUEST

    def __init__(
        self,
        inbox: IInboxStore,
        dlq: IDeadLetterStore,
        *,
        allocator: ISequenceAllocator | None = None,
        uow: IUnitOfWork | None = None,
    ) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._codec = make_codec()
        self._uow: IUnitOfWork = uow or RecordingUoW()
        self._allocator: ISequenceAllocator = allocator or RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def allocator(self) -> ISequenceAllocator:
        return self._allocator


class RelayDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: IOutboxStore,
        transport: ITransport,
        external_mappers: Mapping[str, IEnvelopeMapper[Any, Any]] | None = None,
        uow: IUnitOfWork | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._registry = TransportRegistry({'test': transport}, external_mappers=external_mappers)
        self._uow: IUnitOfWork = uow or RecordingUoW()

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._store

    @provide(scope=Scope.APP)
    def transport_registry(self) -> TransportRegistry:
        return self._registry

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow
