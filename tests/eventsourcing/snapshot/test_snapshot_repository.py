from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, SnapshotTypeMismatchError
from waku.eventsourcing.serialization.json import JsonSnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig, SnapshotConfigRegistry
from waku.eventsourcing.snapshot.repository import SnapshotEventSourcedRepository
from waku.eventsourcing.snapshot.strategy import EventCountStrategy
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.domain import AccountOpened, AccountState, BankAccount, MoneyDeposited


class BankAccountRepository(SnapshotEventSourcedRepository[BankAccount]):
    @override
    def _snapshot_state(self, aggregate: BankAccount) -> object:
        return AccountState(name=aggregate.name, balance=aggregate.balance)

    @override
    def _restore_from_snapshot(self, snapshot: Snapshot) -> BankAccount:
        aggregate = BankAccount()
        aggregate.name = snapshot.state['name']
        aggregate.balance = snapshot.state['balance']
        return aggregate


@pytest.fixture
def event_store() -> InMemoryEventStore:
    registry = EventTypeRegistry()
    registry.register(AccountOpened)
    registry.register(MoneyDeposited)
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
    event_store: InMemoryEventStore,
    snapshot_store: AsyncMock,
    state_serializer: JsonSnapshotStateSerializer,
) -> BankAccountRepository:
    config_registry = SnapshotConfigRegistry({
        'BankAccount': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
    })
    return BankAccountRepository(event_store, snapshot_store, config_registry, state_serializer)


async def test_load_without_snapshot_full_replay(
    repository: BankAccountRepository,
    event_store: InMemoryEventStore,  # noqa: ARG001
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    await repository.save('acc-1', account)

    loaded = await repository.load('acc-1')
    assert loaded.name == 'Alice'
    assert loaded.balance == 100
    assert loaded.version == 1


async def test_load_with_snapshot_partial_replay(
    repository: BankAccountRepository,
    event_store: InMemoryEventStore,  # noqa: ARG001
    snapshot_store: AsyncMock,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    await repository.save('acc-1', account)

    account.deposit(50)
    await repository.save('acc-1', account)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'name': 'Alice', 'balance': 100},
        version=1,
        state_type='BankAccount',
    )

    loaded = await repository.load('acc-1')
    assert loaded.name == 'Alice'
    assert loaded.balance == 150
    assert loaded.version == 2


async def test_load_nonexistent_raises(repository: BankAccountRepository) -> None:
    with pytest.raises(AggregateNotFoundError):
        await repository.load('nonexistent')


async def test_save_triggers_snapshot_at_threshold(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(200)
    await repository.save('acc-1', account)

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.stream_id == StreamId.for_aggregate('BankAccount', 'acc-1')
    assert saved_snapshot.state == {'name': 'Alice', 'balance': 300}
    assert saved_snapshot.version == 2


async def test_save_skips_snapshot_below_threshold(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    await repository.save('acc-1', account)

    snapshot_store.save.assert_not_called()


async def test_multiple_saves_without_reload_triggers_snapshot_at_cumulative_threshold(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    await repository.save('acc-1', account)

    snapshot_store.save.assert_not_called()

    account.deposit(200)
    await repository.save('acc-1', account)

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.stream_id == StreamId.for_aggregate('BankAccount', 'acc-1')
    assert saved_snapshot.state == {'name': 'Alice', 'balance': 300}
    assert saved_snapshot.version == 2


async def test_load_with_mismatched_snapshot_type_raises(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,
) -> None:
    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'name': 'Alice', 'balance': 100},
        version=1,
        state_type='WrongType',
    )

    with pytest.raises(SnapshotTypeMismatchError, match='WrongType'):
        await repository.load('acc-1')


async def test_save_with_no_events_returns_current_version(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,  # noqa: ARG001
) -> None:
    account = BankAccount()
    account.open('Alice')
    await repository.save('acc-1', account)

    loaded = await repository.load('acc-1')
    version, events = await repository.save('acc-1', loaded)

    assert version == 0
    assert events == []


class RenamedBankAccountRepo(SnapshotEventSourcedRepository[BankAccount]):
    aggregate_name = 'Account'

    @override
    def _snapshot_state(self, aggregate: BankAccount) -> object:
        return AccountState(name=aggregate.name, balance=aggregate.balance)

    @override
    def _restore_from_snapshot(self, snapshot: Snapshot) -> BankAccount:
        aggregate = BankAccount()
        aggregate.name = snapshot.state['name']
        aggregate.balance = snapshot.state['balance']
        return aggregate


async def test_snapshot_state_type_matches_aggregate_name(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    config_registry = SnapshotConfigRegistry({
        'Account': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
    })
    repo = RenamedBankAccountRepo(event_store, snapshot_store, config_registry, state_serializer)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('Account', 'acc-1'),
        state={'name': 'Alice', 'balance': 100},
        version=1,
        state_type='Account',
    )

    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(50)
    await repo.save('acc-1', account)

    loaded = await repo.load('acc-1')
    assert loaded.name == 'Alice'
    assert loaded.balance == 150


async def test_snapshot_save_writes_aggregate_name_as_state_type(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    config_registry = SnapshotConfigRegistry({
        'Account': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
    })
    repo = RenamedBankAccountRepo(event_store, snapshot_store, config_registry, state_serializer)

    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(200)
    await repo.save('acc-1', account)

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.state_type == 'Account'


class AddBalanceFieldMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'balance': 0}


async def test_load_with_matching_schema_version_uses_snapshot(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    config_registry = SnapshotConfigRegistry({
        'BankAccount': SnapshotConfig(strategy=EventCountStrategy(threshold=100)),
    })
    repo = BankAccountRepository(event_store, snapshot_store, config_registry, state_serializer)

    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    await repo.save('acc-1', account)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'name': 'Alice', 'balance': 100},
        version=1,
        state_type='BankAccount',
        schema_version=1,
    )

    loaded = await repo.load('acc-1')

    assert loaded.name == 'Alice'
    assert loaded.balance == 100


async def test_load_with_old_schema_version_applies_migration(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    config_registry = SnapshotConfigRegistry({
        'BankAccount': SnapshotConfig(
            strategy=EventCountStrategy(threshold=100),
            schema_version=2,
            migration_chain=SnapshotMigrationChain([AddBalanceFieldMigration()]),
        ),
    })
    repo = BankAccountRepository(event_store, snapshot_store, config_registry, state_serializer)

    account = BankAccount()
    account.open('Alice')
    account.deposit(50)
    await repo.save('acc-1', account)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'name': 'Alice'},
        version=1,
        state_type='BankAccount',
        schema_version=1,
    )

    loaded = await repo.load('acc-1')

    assert loaded.name == 'Alice'
    assert loaded.balance == 0


async def test_load_with_old_schema_version_no_migration_replays_from_events(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    config_registry = SnapshotConfigRegistry({
        'BankAccount': SnapshotConfig(
            strategy=EventCountStrategy(threshold=100),
            schema_version=3,
            migration_chain=SnapshotMigrationChain([AddBalanceFieldMigration()]),
        ),
    })
    repo = BankAccountRepository(event_store, snapshot_store, config_registry, state_serializer)

    account = BankAccount()
    account.open('Alice')
    account.deposit(200)
    await repo.save('acc-1', account)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'name': 'Alice'},
        version=1,
        state_type='BankAccount',
        schema_version=1,
    )

    loaded = await repo.load('acc-1')

    assert loaded.name == 'Alice'
    assert loaded.balance == 200


async def test_save_writes_current_schema_version(
    mocker: MockerFixture,
    event_store: InMemoryEventStore,
    state_serializer: JsonSnapshotStateSerializer,
) -> None:
    snapshot_store = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    config_registry = SnapshotConfigRegistry({
        'BankAccount': SnapshotConfig(
            strategy=EventCountStrategy(threshold=3),
            schema_version=2,
            migration_chain=SnapshotMigrationChain([AddBalanceFieldMigration()]),
        ),
    })
    repo = BankAccountRepository(event_store, snapshot_store, config_registry, state_serializer)

    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(200)
    await repo.save('acc-1', account)

    snapshot_store.save.assert_called_once()
    saved_snapshot: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved_snapshot.schema_version == 2


async def test_snapshot_save_failure_does_not_prevent_aggregate_save(
    repository: BankAccountRepository,
    event_store: InMemoryEventStore,
    snapshot_store: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot_store.save.side_effect = RuntimeError('snapshot store unavailable')

    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(200)
    version, events = await repository.save('acc-1', account)

    assert version == 2
    assert len(events) == 3
    stored = await event_store.read_stream(StreamId.for_aggregate('BankAccount', 'acc-1'))
    assert len(stored) == 3
    assert 'Failed to save snapshot' in caplog.text
