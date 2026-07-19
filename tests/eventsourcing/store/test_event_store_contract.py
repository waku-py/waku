from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from typing_extensions import override

from waku.backends.sqlalchemy.event_store.store import SqlAlchemyEventStore
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.backends.testing import EventStoreContract, ItemAdded, OrderCreated, make_envelope
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import (
    ConcurrencyConflictError,
    PartialDuplicateAppendError,
    StreamArchivedError,
)
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.exceptions import ImproperlyConfiguredError
from waku.serialization.upcasting.chain import UpcasterChain

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pytest_mock import MockerFixture
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.backends.sqlalchemy.event_store.tables import EventStoreTables
    from waku.backends.testing import EventStoreFactory
    from waku.eventsourcing.contracts.event import IMetadataEnricher
    from waku.eventsourcing.contracts.stream import ExpectedVersion
    from waku.eventsourcing.projection.interfaces import IProjection
    from waku.eventsourcing.serialization.registry import EventTypeRegistry
    from waku.eventsourcing.store.interfaces import IEventStore

# The exported conformance kit carries the portable behavioral contract; this suite subscribes the
# in-memory reference store and the SQLAlchemy adapter, pinning fake == real. The module-level tests
# below are SQLAlchemy-implementation-specific (session usability, savepoint races) or pin the two
# concrete stores' facet construction — they are deliberately NOT part of the exported kit.


class TestEventStoreContract(EventStoreContract):
    @pytest.fixture(params=['in_memory', 'sqlalchemy'])
    @override
    def store_factory(self, request: pytest.FixtureRequest, registry: EventTypeRegistry) -> EventStoreFactory:
        if request.param == 'in_memory':

            def _in_memory(
                projections: Sequence[IProjection] = (),
                enrichers: Sequence[IMetadataEnricher] = (),
            ) -> IEventStore:
                return InMemoryEventStore(registry=registry, projections=projections, enrichers=enrichers)

            return _in_memory

        pg_session: AsyncSession = request.getfixturevalue('pg_session')
        serializer = JsonEventSerializer(registry)
        tables = bind_event_store_tables(MetaData())

        def _sqlalchemy(
            projections: Sequence[IProjection] = (),
            enrichers: Sequence[IMetadataEnricher] = (),
        ) -> IEventStore:
            return SqlAlchemyEventStore(
                session=pg_session,
                serializer=serializer,
                registry=registry,
                tables=tables,
                upcaster_chain=UpcasterChain({}),
                projections=projections,
                enrichers=enrichers,
            )

        return _sqlalchemy


# ── SQLAlchemy-specific behaviors (conftest fixtures over the sample domain in tests/) ─────────────


def _skip_if_in_memory(request: pytest.FixtureRequest, reason: str) -> None:
    callspec = getattr(request.node, 'callspec', None)
    if callspec is not None and 'in_memory' in callspec.id:
        pytest.skip(reason)


def _patch_idempotency_first_call_returns_none(mocker: MockerFixture, store: IEventStore) -> None:
    assert isinstance(store, SqlAlchemyEventStore), 'This helper only works with SqlAlchemyEventStore'
    original = SqlAlchemyEventStore._check_idempotency  # noqa: SLF001
    call_count = 0

    async def _side_effect(
        stream_id: StreamId,
        events: Sequence[EventEnvelope],
    ) -> int | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return await original(store, stream_id, events)

    mocker.patch.object(store, '_check_idempotency', side_effect=_side_effect)


async def _seed_and_arm_savepoint_race(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> tuple[list[EventEnvelope], int]:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')
    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]
    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    _patch_idempotency_first_call_returns_none(mocker, store)
    return envelopes, original_version


async def test_session_remains_usable_after_idempotent_append(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'session usability is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]

    await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 2


async def test_stream_state_consistent_after_idempotent_append(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint consistency is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]

    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    idempotent_version = await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))

    assert idempotent_version == original_version

    events = await store.read_stream(stream_id)
    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='123')
    assert events[0].idempotency_key == 'key-1'
    assert events[1].data == ItemAdded(item_name='Widget')
    assert events[1].idempotency_key == 'key-2'

    idempotency_keys = [e.idempotency_key for e in events]
    assert len(idempotency_keys) == len(set(idempotency_keys))


async def test_session_remains_usable_after_partial_duplicate_error(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'session usability is only relevant for SQLAlchemy store')

    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1')],
        expected_version=NoStream(),
    )

    with pytest.raises(PartialDuplicateAppendError):
        await store.append_to_stream(
            stream_id,
            [
                EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
                EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-new'),
            ],
            expected_version=Exact(version=0),
        )

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 1
    assert events[0].idempotency_key == 'key-1'


async def test_savepoint_race_with_all_keys_returns_idempotent_version(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes, original_version = await _seed_and_arm_savepoint_race(request, mocker, store, stream_id)
    version = await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=original_version))

    assert version == original_version
    events = await store.read_stream(stream_id)
    assert len(events) == 2
    assert [e.idempotency_key for e in events] == ['key-1', 'key-2']


async def test_savepoint_race_with_partial_keys_raises_partial_duplicate(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')

    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1')],
        expected_version=NoStream(),
    )

    partial_envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-new'),
    ]

    _patch_idempotency_first_call_returns_none(mocker, store)
    with pytest.raises(PartialDuplicateAppendError):
        await store.append_to_stream(stream_id, partial_envelopes, expected_version=Exact(version=0))

    events = await store.read_stream(stream_id)
    assert len(events) == 1
    assert events[0].idempotency_key == 'key-1'


async def test_session_usable_after_savepoint_race_recovery(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes, original_version = await _seed_and_arm_savepoint_race(request, mocker, store, stream_id)
    await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=original_version))

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 2

    other_stream = StreamId.for_aggregate('Order', 'other')
    version = await store.append_to_stream(
        other_stream,
        [make_envelope(OrderCreated(order_id='other'))],
        expected_version=NoStream(),
    )
    assert version == 0
    assert await store.stream_exists(other_stream) is True


async def test_stream_version_consistent_after_savepoint_race_recovery(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes, original_version = await _seed_and_arm_savepoint_race(request, mocker, store, stream_id)
    recovered_version = await store.append_to_stream(
        stream_id,
        envelopes,
        expected_version=Exact(version=original_version),
    )

    assert recovered_version == original_version

    events = await store.read_stream(stream_id)
    assert len(events) == original_version + 1
    assert events[0].position == 0
    assert events[1].position == 1
    assert events[0].data == OrderCreated(order_id='123')
    assert events[1].data == ItemAdded(item_name='Widget')


# --- read_all event_types filtering ---


def test_in_memory_store_exposes_the_exact_composed_facets(registry: EventTypeRegistry) -> None:
    snapshots = InMemorySnapshotStore()
    checkpoints = InMemoryCheckpointStore()

    store = InMemoryEventStore(registry=registry, snapshots=snapshots, checkpoints=checkpoints)

    assert store.snapshots is snapshots
    assert store.checkpoints is checkpoints


def test_sqlalchemy_store_without_facets_rejects_facet_access(
    registry: EventTypeRegistry,
    mocker: MockerFixture,
) -> None:
    store = SqlAlchemyEventStore(
        session=mocker.Mock(),
        serializer=JsonEventSerializer(registry),
        registry=registry,
        tables=bind_event_store_tables(MetaData()),
        upcaster_chain=UpcasterChain({}),
    )

    with pytest.raises(ImproperlyConfiguredError, match='snapshots facet'):
        _ = store.snapshots
    with pytest.raises(ImproperlyConfiguredError, match='checkpoints facet'):
        _ = store.checkpoints


def test_in_memory_store_without_facets_rejects_facet_access(registry: EventTypeRegistry) -> None:
    store = InMemoryEventStore(registry=registry)

    with pytest.raises(ImproperlyConfiguredError, match='snapshots facet'):
        _ = store.snapshots
    with pytest.raises(ImproperlyConfiguredError, match='checkpoints facet'):
        _ = store.checkpoints


# ── Two-session Postgres contention (real READ COMMITTED, SQLAlchemy only) ─────────────────────────


def _sql_store(
    session: AsyncSession,
    registry: EventTypeRegistry,
    tables: EventStoreTables,
) -> SqlAlchemyEventStore:
    return SqlAlchemyEventStore(
        session=session,
        serializer=JsonEventSerializer(registry),
        registry=registry,
        tables=tables,
        upcaster_chain=UpcasterChain({}),
    )


def _arm_after_version_read(
    mocker: MockerFixture,
    store: SqlAlchemyEventStore,
    interleave: Callable[[], Awaitable[None]],
) -> None:
    # Fire ``interleave`` once, in the append's race window: after the live version is read and the
    # archived fast-path check passes, but before the version-advancing conditional UPDATE runs.
    original = SqlAlchemyEventStore._resolve_current_version  # noqa: SLF001

    async def _side_effect(stream_id: StreamId, expected_version: ExpectedVersion) -> int:
        version = await original(store, stream_id, expected_version)
        await interleave()
        return version

    mocker.patch.object(store, '_resolve_current_version', side_effect=_side_effect)


async def test_append_racing_a_committed_archive_raises_stream_archived(
    pg_session_pair: tuple[AsyncSession, AsyncSession],
    registry: EventTypeRegistry,
    mocker: MockerFixture,
) -> None:
    session_a, session_b = pg_session_pair
    tables = bind_event_store_tables(MetaData())
    store_a = _sql_store(session_a, registry, tables)
    store_b = _sql_store(session_b, registry, tables)
    stream_id = StreamId.for_aggregate('Order', 'race')

    await store_a.append_to_stream(stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream())
    await session_a.commit()

    async def _archive_and_commit() -> None:
        await store_b.archive_stream(stream_id)
        await session_b.commit()

    _arm_after_version_read(mocker, store_a, _archive_and_commit)

    with pytest.raises(StreamArchivedError):
        await store_a.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='post-archive'))],
            expected_version=Exact(0),
        )
    await session_a.rollback()

    surviving = await store_a.read_stream(stream_id)
    assert len(surviving) == 1


async def test_concurrent_same_version_appends_raise_concurrency_for_loser(
    pg_session_pair: tuple[AsyncSession, AsyncSession],
    registry: EventTypeRegistry,
    mocker: MockerFixture,
) -> None:
    session_a, session_b = pg_session_pair
    tables = bind_event_store_tables(MetaData())
    store_a = _sql_store(session_a, registry, tables)
    store_b = _sql_store(session_b, registry, tables)
    stream_id = StreamId.for_aggregate('Order', 'contend')

    await store_a.append_to_stream(stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream())
    await session_a.commit()

    async def _winner_appends_and_commits() -> None:
        await store_b.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='winner'))],
            expected_version=Exact(0),
        )
        await session_b.commit()

    _arm_after_version_read(mocker, store_a, _winner_appends_and_commits)

    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await store_a.append_to_stream(
            stream_id,
            [
                make_envelope(ItemAdded(item_name='loser-1')),
                make_envelope(ItemAdded(item_name='loser-2')),
            ],
            expected_version=Exact(0),
        )
    await session_a.rollback()

    # The loser targets two events (new_version=2) but the winner committed one (actual=1): the raised
    # actual_version must report the winner's real committed version, not the loser's stale append target.
    assert exc_info.value.actual_version == 1

    events = await store_a.read_stream(stream_id)
    assert len(events) == 2
    assert events[1].data == ItemAdded(item_name='winner')


async def test_concurrent_appends_to_distinct_streams_share_one_global_position_order(
    pg_session_pair: tuple[AsyncSession, AsyncSession],
    registry: EventTypeRegistry,
    mocker: MockerFixture,
) -> None:
    session_a, session_b = pg_session_pair
    tables = bind_event_store_tables(MetaData())
    store_a = _sql_store(session_a, registry, tables)
    store_b = _sql_store(session_b, registry, tables)
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')

    async def _append_b_and_commit() -> None:
        await store_b.append_to_stream(
            stream_b,
            [make_envelope(OrderCreated(order_id='B'))],
            expected_version=NoStream(),
        )
        await session_b.commit()

    # Interleave B's whole append+commit inside A's append window so the two appenders allocate global
    # positions from the shared IDENTITY sequence concurrently, not in program order.
    _arm_after_version_read(mocker, store_a, _append_b_and_commit)

    await store_a.append_to_stream(
        stream_a,
        [make_envelope(OrderCreated(order_id='A'))],
        expected_version=NoStream(),
    )
    await session_a.commit()

    # read_all must merge both streams into ONE gapless, ascending total order — the invariant outbox
    # relays and projections watermark on. B allocated first (inside the window), so it leads.
    combined = await store_a.read_all()
    positions = [event.global_position for event in combined]

    assert [event.data for event in combined] == [OrderCreated(order_id='B'), OrderCreated(order_id='A')]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    assert positions[1] == positions[0] + 1
