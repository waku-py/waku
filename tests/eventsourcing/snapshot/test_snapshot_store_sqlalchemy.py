from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.event_store.store import SqlAlchemyEventStore
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.backends.sqlalchemy.snapshot.store import SqlAlchemySnapshotStore
from waku.backends.sqlalchemy.snapshot.tables import bind_snapshot_tables
from waku.backends.testing import ItemAdded, OrderCreated, make_envelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.serialization.json import JsonEventSerializer, JsonSnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot._internal.manager import SnapshotManager
from waku.eventsourcing.snapshot.registry import SnapshotConfig
from waku.eventsourcing.snapshot.strategy import EventCountStrategy
from waku.serialization.upcasting.chain import UpcasterChain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# The ``version`` column is int4; a value past its range is rejected server-side ("integer out of range")
# during the real snapshot INSERT — a genuine aborted PG transaction, not a client-side raise. (SQLAlchemy's
# JSONB renders Python ``None`` as JSON ``null`` under its ``none_as_null=False`` default, so a null state
# never trips the ``state NOT NULL`` constraint; the int4 overflow is the reliable server-side lever.)
_INT4_OVERFLOW = 2**31


@dataclass(frozen=True, slots=True)
class _State:
    key: str = 'value'


def _event_store(session: AsyncSession, registry: EventTypeRegistry) -> SqlAlchemyEventStore:
    return SqlAlchemyEventStore(
        session=session,
        serializer=JsonEventSerializer(registry),
        registry=registry,
        tables=bind_event_store_tables(MetaData()),
        upcaster_chain=UpcasterChain({}),
    )


def _snapshot_manager(session: AsyncSession) -> SnapshotManager:
    return SnapshotManager(
        store=SqlAlchemySnapshotStore(session=session, snapshots_table=bind_snapshot_tables(MetaData()).snapshots),
        config=SnapshotConfig(strategy=EventCountStrategy(threshold=1)),
        valid_state_types=frozenset({'Order'}),
        serializer=JsonSnapshotStateSerializer(),
    )


async def test_snapshot_write_failure_leaves_appended_events_committable(
    pg_session: AsyncSession,
    pg_engine: AsyncEngine,
) -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.register(ItemAdded)
    stream_id = StreamId.for_aggregate('Order', 'iso-1')

    event_store = _event_store(pg_session, registry)
    await event_store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='iso-1')), make_envelope(ItemAdded(item_name='Widget'))],
        expected_version=NoStream(),
    )

    manager = _snapshot_manager(pg_session)
    # The manager swallows the store failure (rebuildable-cache policy); the adapter savepoint must keep the
    # shared transaction committable so the source-of-truth events appended before it survive.
    await manager.save_snapshot(stream_id, 'iso-1', _State, version=_INT4_OVERFLOW, state_type_name='Order')

    await pg_session.commit()

    async with AsyncSession(pg_engine, expire_on_commit=False) as fresh:
        events = await _event_store(fresh, registry).read_stream(stream_id)
        fresh_snapshots = SqlAlchemySnapshotStore(
            session=fresh,
            snapshots_table=bind_snapshot_tables(MetaData()).snapshots,
        )
        snapshot = await fresh_snapshots.load(stream_id)

    assert [type(event.data) for event in events] == [OrderCreated, ItemAdded]
    assert snapshot is None


async def test_snapshot_update_failure_retains_prior_stale_snapshot(
    pg_session: AsyncSession,
    pg_engine: AsyncEngine,
) -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.register(ItemAdded)
    stream_id = StreamId.for_aggregate('Order', 'iso-2')

    event_store = _event_store(pg_session, registry)
    await event_store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='iso-2')), make_envelope(ItemAdded(item_name='Widget'))],
        expected_version=NoStream(),
    )

    manager = _snapshot_manager(pg_session)
    # Seed a valid prior snapshot, then drive a failing ``on_conflict_do_update`` at the same stream_id. The
    # savepoint must roll back only the rejected update, retaining the earlier (now stale) row while the
    # durable events still commit.
    await manager.save_snapshot(stream_id, 'iso-2', _State, version=1, state_type_name='Order')
    await manager.save_snapshot(stream_id, 'iso-2', _State, version=_INT4_OVERFLOW, state_type_name='Order')

    await pg_session.commit()

    async with AsyncSession(pg_engine, expire_on_commit=False) as fresh:
        events = await _event_store(fresh, registry).read_stream(stream_id)
        fresh_snapshots = SqlAlchemySnapshotStore(
            session=fresh,
            snapshots_table=bind_snapshot_tables(MetaData()).snapshots,
        )
        snapshot = await fresh_snapshots.load(stream_id)

    assert [type(event.data) for event in events] == [OrderCreated, ItemAdded]
    assert snapshot is not None
    assert snapshot.version == 1
