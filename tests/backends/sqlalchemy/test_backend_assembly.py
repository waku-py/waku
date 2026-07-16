from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  # dishka introspects the session factory signature
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku import module
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.messaging import MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.outbox.models import OutboxMessage
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.eventsourcing.domain import NoteCreated, NoteRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.application import WakuApplication


def _outbox_message(destination: str = 'test://notes') -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(uuid4()),
        message_type='tests.Note',
        payload={'title': 'assembled'},
        destination=destination,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
    )


@pytest.fixture
async def assembled_app(pg_engine: AsyncEngine) -> AsyncIterator[WakuApplication]:
    metadata = MetaData()

    async def _session_factory() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(pg_engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    es_ext = EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated])

    @module(extensions=[es_ext])
    class DomainModule:
        pass

    try:
        async with create_test_app(
            imports=[
                MessagingModule.register(
                    MessagingConfig(
                        outbox=OutboxConfig(),
                        inbox=InboxConfig(owner_id='assembly-node:1'),
                        dead_letter=DeadLetterConfig(),
                    ),
                ),
                EventSourcingModule.register(EventSourcingConfig(event_serializer=JsonEventSerializer)),
                SqlAlchemyBackend.register(session_factory=_session_factory, metadata=metadata),
                DomainModule,
            ],
        ) as app:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            yield app
    finally:
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


async def test_both_composites_resolve_and_expose_the_scope_facet_ports(assembled_app: WakuApplication) -> None:
    async with assembled_app.container() as scope:
        durability = await scope.get(IDurabilityStore)
        event_store = await scope.get(IEventStore)

        assert durability.outbox is await scope.get(IOutboxStore)
        assert durability.inbox is await scope.get(IInboxStore)
        assert durability.dead_letters is await scope.get(IDeadLetterStore)
        assert durability.unit_of_work is await scope.get(IUnitOfWork)
        assert event_store.snapshots is await scope.get(ISnapshotStore)
        assert event_store.checkpoints is await scope.get(ICheckpointStore)


async def test_append_and_forward_roll_back_together_across_the_sibling_seam(
    assembled_app: WakuApplication,
) -> None:
    stream_id = StreamId.for_aggregate('Note', 'atomic-1')
    message = _outbox_message()

    async with assembled_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)

        await event_store.append_to_stream(
            stream_id,
            [EventEnvelope(domain_event=NoteCreated(title='atomic'), idempotency_key=str(uuid4()))],
            expected_version=NoStream(),
        )
        await outbox.save_batch([message])
        await uow.rollback()

    async with assembled_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)

        assert await event_store.stream_exists(stream_id) is False
        assert await outbox.fetch_head_of_queue(batch_size=10) == []


async def test_append_and_forward_commit_together(assembled_app: WakuApplication) -> None:
    stream_id = StreamId.for_aggregate('Note', 'atomic-2')
    message = _outbox_message()

    async with assembled_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)

        await event_store.append_to_stream(
            stream_id,
            [EventEnvelope(domain_event=NoteCreated(title='atomic'), idempotency_key=str(uuid4()))],
            expected_version=NoStream(),
        )
        await outbox.save_batch([message])
        await uow.commit()

    async with assembled_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)

        assert await event_store.stream_exists(stream_id) is True
        fetched = await outbox.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in fetched] == [message.id]


async def test_register_with_metadata_binds_the_sequences_table_when_messaging_is_active() -> None:
    metadata = MetaData()

    def _session_factory() -> AsyncSession:  # pragma: no cover - never resolved
        return AsyncSession()

    async with create_test_app(
        imports=[
            MessagingModule.register(MessagingConfig()),
            SqlAlchemyBackend.register(session_factory=_session_factory, metadata=metadata),
        ],
    ):
        assert 'message_sequences' in metadata.tables
