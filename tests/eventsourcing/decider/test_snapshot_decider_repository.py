from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.decider.repository import SnapshotDeciderRepository
from waku.eventsourcing.exceptions import SnapshotTypeMismatchError
from waku.eventsourcing.serialization.json import JsonSnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig, SnapshotConfigRegistry
from waku.eventsourcing.snapshot.strategy import EventCountStrategy
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented


class CounterSnapshotRepository(SnapshotDeciderRepository[CounterState, Increment, Incremented]):
    aggregate_name = 'Counter'


@pytest.fixture
def decider() -> CounterDecider:
    return CounterDecider()


@pytest.fixture
def event_store() -> InMemoryEventStore:
    registry = EventTypeRegistry()
    registry.register(Incremented)
    return InMemoryEventStore(registry=registry)


@pytest.fixture
def snapshot_store(mocker: MockerFixture) -> AsyncMock:
    mock: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    mock.load.return_value = None
    return mock


@pytest.fixture
def state_serializer() -> JsonSnapshotStateSerializer:
    return JsonSnapshotStateSerializer()


@pytest.fixture
def repository(
    decider: CounterDecider,
    event_store: InMemoryEventStore,
    snapshot_store: AsyncMock,
    state_serializer: JsonSnapshotStateSerializer,
) -> CounterSnapshotRepository:
    strategy = EventCountStrategy(threshold=3)
    registry = SnapshotConfigRegistry({'Counter': SnapshotConfig(strategy=strategy)})
    return CounterSnapshotRepository(
        decider=decider,
        event_store=event_store,
        snapshot_store=snapshot_store,
        snapshot_config_registry=registry,
        state_serializer=state_serializer,
    )


async def test_load_without_snapshot_falls_back_to_full_replay(
    repository: CounterSnapshotRepository,
) -> None:
    await repository.save('c-1', [Incremented(amount=2), Incremented(amount=3)], expected_version=-1)

    state, version = await repository.load('c-1')

    assert state == CounterState(value=5)
    assert version == 1


async def test_load_with_snapshot_applies_delta_replay(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    await repository.save(
        'c-2',
        [Incremented(amount=1), Incremented(amount=2)],
        expected_version=-1,
    )
    await repository.save(
        'c-2',
        [Incremented(amount=3)],
        expected_version=1,
    )

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('Counter', 'c-2'),
        state={'value': 3},
        version=1,
        state_type='CounterState',
    )

    state, version = await repository.load('c-2')

    assert state == CounterState(value=6)
    assert version == 2


async def test_save_triggers_snapshot_when_strategy_says_yes(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    await repository.save(
        'c-3',
        [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)],
        expected_version=-1,
    )

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.stream_id == StreamId.for_aggregate('Counter', 'c-3')
    assert saved_snapshot.version == 2
    assert saved_snapshot.state == {'value': 6}


async def test_save_uses_provided_state_for_snapshot(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    state = CounterState(value=6)

    await repository.save(
        'c-3',
        [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)],
        expected_version=-1,
        current_state=state,
    )

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.state == {'value': 6}


async def test_save_skips_snapshot_when_strategy_says_no(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    await repository.save(
        'c-4',
        [Incremented(amount=1), Incremented(amount=2)],
        expected_version=-1,
    )

    snapshot_store.save.assert_not_called()


async def test_snapshot_stores_correct_metadata(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    await repository.save(
        'c-5',
        [Incremented(amount=10), Incremented(amount=20), Incremented(amount=30)],
        expected_version=-1,
    )

    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.state_type == 'CounterState'
    assert saved_snapshot.stream_id == StreamId.for_aggregate('Counter', 'c-5')
    assert saved_snapshot.version == 2


async def test_load_with_mismatched_snapshot_type_raises(
    repository: CounterSnapshotRepository,
    snapshot_store: AsyncMock,
) -> None:
    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'),
        state={'value': 5},
        version=1,
        state_type='WrongState',
    )

    with pytest.raises(SnapshotTypeMismatchError, match='WrongState'):
        await repository.load('c-1')


async def test_snapshot_save_failure_does_not_prevent_aggregate_save(
    repository: CounterSnapshotRepository,
    event_store: InMemoryEventStore,
    snapshot_store: AsyncMock,
) -> None:
    snapshot_store.save.side_effect = RuntimeError('snapshot store unavailable')

    version = await repository.save(
        'c-1',
        [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)],
        expected_version=-1,
    )

    assert version == 2
    stored = await event_store.read_stream(StreamId.for_aggregate('Counter', 'c-1'))
    assert len(stored) == 3


class AddDefaultValueMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'value': state.get('value', 0)}


async def test_load_with_old_schema_version_applies_migration(
    mocker: MockerFixture,
    decider: CounterDecider,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    strategy = EventCountStrategy(threshold=100)
    registry = SnapshotConfigRegistry({
        'Counter': SnapshotConfig(
            strategy=strategy,
            schema_version=2,
            migration_chain=SnapshotMigrationChain([AddDefaultValueMigration()]),
        ),
    })
    repo = CounterSnapshotRepository(
        decider=decider,
        event_store=event_store,
        snapshot_store=snapshot_store,
        snapshot_config_registry=registry,
        state_serializer=state_serializer,
    )

    await repo.save('c-1', [Incremented(amount=5), Incremented(amount=3)], expected_version=-1)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'),
        state={'value': 5},
        version=0,
        state_type='CounterState',
        schema_version=1,
    )

    state, version = await repo.load('c-1')

    assert state == CounterState(value=8)
    assert version == 1


async def test_load_with_old_schema_version_no_migration_replays_from_events(
    mocker: MockerFixture,
    decider: CounterDecider,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    strategy = EventCountStrategy(threshold=100)
    registry = SnapshotConfigRegistry({
        'Counter': SnapshotConfig(
            strategy=strategy,
            schema_version=3,
            migration_chain=SnapshotMigrationChain([AddDefaultValueMigration()]),
        ),
    })
    repo = CounterSnapshotRepository(
        decider=decider,
        event_store=event_store,
        snapshot_store=snapshot_store,
        snapshot_config_registry=registry,
        state_serializer=state_serializer,
    )

    await repo.save('c-1', [Incremented(amount=5), Incremented(amount=3)], expected_version=-1)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'),
        state={'value': 5},
        version=0,
        state_type='CounterState',
        schema_version=1,
    )

    state, version = await repo.load('c-1')

    assert state == CounterState(value=8)
    assert version == 1
