from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from typing_extensions import override

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.di import object_, scoped
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
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
)
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.outbox import OutboxRelayConfig
from waku.messaging.router import external_endpoint, local_queue, route
from waku.messaging.sequence import ISequenceAllocator
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
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

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

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.rows[entry.id] = entry

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        return self.rows[entry_id]

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:
        self.rows[entry_id] = replace(self.rows[entry_id], status=DeadLetterStatus.REPLAYED)

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:  # pragma: no cover
        self.rows[entry_id] = replace(self.rows[entry_id], status=DeadLetterStatus.REPLAY_FAILED)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def claim_replayable(
        self, batch_size: int, max_replay_count: int
    ) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        self.rows.pop(entry_id, None)

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0


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
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(InMemoryOutboxStore(dl_store), provided_type=IOutboxStore),
                object_(InMemoryInboxStore(dl_store), provided_type=IInboxStore),
                object_(dl_store, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.send(_Charge(amount=42))
        await wait_until(lambda: bool(dl_store.rows))

        entry_id = next(iter(dl_store.rows))
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay_by_id(entry_id) is True
        await wait_until(lambda: len(_attempts) == 2)

    assert _attempts == [42, 42]
    assert dl_store.rows[entry_id].status is DeadLetterStatus.REPLAYED


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
    outbox = InMemoryOutboxStore(dlq)
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
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(outbox, provided_type=IOutboxStore),
                object_(InMemoryInboxStore(dlq), provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
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
    inbox = InMemoryInboxStore(dlq)
    config = MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(owner_id='test-node:1'),
        dead_letter=DeadLetterConfig(),
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().requeue(max_attempts=1),)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_TenantRecordingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(InMemoryOutboxStore(dlq), provided_type=IOutboxStore),
                object_(inbox, provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
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
    outbox = InMemoryOutboxStore(dlq)
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
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(outbox, provided_type=IOutboxStore),
                object_(InMemoryInboxStore(dlq), provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
            ],
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
    inbox = InMemoryInboxStore(dlq)
    config = MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(owner_id='test-node:1'),
        dead_letter=DeadLetterConfig(),
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().requeue(max_attempts=1),)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FlakyOrderHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(InMemoryOutboxStore(dlq), provided_type=IOutboxStore),
                object_(inbox, provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
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

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
            providers=[
                scoped(IUnitOfWork, _ScopedRecordingUoW),
                object_(InMemoryOutboxStore(dlq), provided_type=IOutboxStore),
                object_(InMemoryInboxStore(dlq), provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                scoped(IDurabilityStore, _durability),
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
