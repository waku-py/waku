from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import pytest

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.messages import IEvent
from waku.messaging.config import MessagingConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.modules import MessagingModule
from waku.messaging.outbox.models import OutboxMessage
from waku.modules._internal.metadata import DynamicModule, module
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import WakuApplication

__all__ = ['BackendAssemblyContract']


@dataclass(frozen=True)
class ConformanceNoteCreated(IEvent):
    title: str


class ConformanceNote(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ''

    def create(self, title: str) -> None:
        self._raise_event(ConformanceNoteCreated(title=title))

    def _apply(self, event: IEvent) -> None:
        match event:
            case ConformanceNoteCreated(title=title):
                self.title = title


class ConformanceNoteRepository(EventSourcedRepository[ConformanceNote]):
    aggregate_name = 'ConformanceNote'


def _outbox_message() -> OutboxMessage:
    return OutboxMessage(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        message_type='conformance.Note',
        payload={'title': 'assembled'},
        destination='test://conformance',
        correlation_id=str(uuid.uuid4()),
        causation_id=str(uuid.uuid4()),
    )


class BackendAssemblyContract:
    """Whole-backend assembly contract: both store objects over ONE resource, atomic sibling seam.

    Subclass in your backend's test suite and override the ``backend_module`` fixture to return
    your registered backend (plus any resource setup/teardown around the yield). Backends whose
    ``IUnitOfWork`` cannot stage-and-commit/roll-back real writes (e.g. an in-memory wiring stub)
    opt out of the atomicity assertions (commit-together and roll-back-together) with
    ``supports_rollback = False``.
    """

    supports_rollback: ClassVar[bool] = True

    @pytest.fixture
    def backend_module(self) -> DynamicModule:
        msg = 'override the backend_module fixture with your registered backend'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    async def app(self, backend_module: DynamicModule) -> AsyncIterator[WakuApplication]:
        es_ext = EventSourcingExtension().bind_aggregate(
            repository=ConformanceNoteRepository,
            event_types=[ConformanceNoteCreated],
        )

        @module(extensions=[es_ext])
        class ConformanceDomainModule:
            pass

        async with create_test_app(
            imports=[
                # A bare MessagingConfig wires the store PORTS (the backend provides them
                # unconditionally) without starting the outbox relay / inbox drainer, whose
                # background polling would race the atomicity assertions' cross-scope read-back.
                # The assembly contract proves store assembly over one resource, not worker lifecycle.
                MessagingModule.register(MessagingConfig()),
                EventSourcingModule.register(EventSourcingConfig(event_serializer=JsonEventSerializer)),
                backend_module,
                ConformanceDomainModule,
            ],
        ) as app:
            yield app

    async def test_composites_expose_the_scope_facet_ports(self, app: WakuApplication) -> None:
        async with app.container() as scope:
            durability = await scope.get(IDurabilityStore)
            event_store = await scope.get(IEventStore)

            assert durability.outbox is await scope.get(IOutboxStore)
            assert durability.inbox is await scope.get(IInboxStore)
            assert durability.dead_letters is await scope.get(IDeadLetterStore)
            assert event_store.snapshots is await scope.get(ISnapshotStore)
            assert event_store.checkpoints is await scope.get(ICheckpointStore)

    async def test_append_and_forward_roll_back_together(self, app: WakuApplication) -> None:
        if not self.supports_rollback:
            pytest.skip('backend opts out: its IUnitOfWork does not stage-and-roll-back real writes')
        stream_id = StreamId.for_aggregate('ConformanceNote', str(uuid.uuid4()))
        message = _outbox_message()

        async with app.container() as scope:
            event_store = await scope.get(IEventStore)
            outbox = await scope.get(IOutboxStore)
            uow = await scope.get(IUnitOfWork)

            await event_store.append_to_stream(
                stream_id,
                [EventEnvelope(domain_event=ConformanceNoteCreated(title='atomic'), idempotency_key=str(uuid.uuid4()))],
                expected_version=NoStream(),
            )
            await outbox.save_batch([message])
            await uow.rollback()

        async with app.container() as scope:
            event_store = await scope.get(IEventStore)
            outbox = await scope.get(IOutboxStore)

            assert await event_store.stream_exists(stream_id) is False
            assert await outbox.fetch_head_of_queue(batch_size=10) == []

    async def test_append_and_forward_commit_together(self, app: WakuApplication) -> None:
        if not self.supports_rollback:
            pytest.skip('backend opts out: its IUnitOfWork does not stage-and-commit real writes')
        stream_id = StreamId.for_aggregate('ConformanceNote', str(uuid.uuid4()))
        message = _outbox_message()

        async with app.container() as scope:
            event_store = await scope.get(IEventStore)
            outbox = await scope.get(IOutboxStore)
            uow = await scope.get(IUnitOfWork)

            await event_store.append_to_stream(
                stream_id,
                [EventEnvelope(domain_event=ConformanceNoteCreated(title='atomic'), idempotency_key=str(uuid.uuid4()))],
                expected_version=NoStream(),
            )
            await outbox.save_batch([message])
            await uow.commit()

        async with app.container() as scope:
            event_store = await scope.get(IEventStore)
            outbox = await scope.get(IOutboxStore)

            assert await event_store.stream_exists(stream_id) is True
            assert [m.id for m in await outbox.fetch_head_of_queue(batch_size=10)] == [message.id]
