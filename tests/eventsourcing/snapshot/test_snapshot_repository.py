from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import AggregateNotFoundError, SnapshotTypeMismatchError
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot
from waku.eventsourcing.snapshot.repository import SnapshotEventSourcedRepository
from waku.eventsourcing.snapshot.serialization import JsonSnapshotStateSerializer
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
def snapshot_store() -> AsyncMock:
    mock = AsyncMock(spec=ISnapshotStore)
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
    strategy = EventCountStrategy(threshold=3)
    return BankAccountRepository(event_store, snapshot_store, strategy, state_serializer)


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
        stream_id='BankAccount-acc-1',
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
    assert saved_snapshot.stream_id == 'BankAccount-acc-1'
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
    assert saved_snapshot.stream_id == 'BankAccount-acc-1'
    assert saved_snapshot.state == {'name': 'Alice', 'balance': 300}
    assert saved_snapshot.version == 2


async def test_load_with_mismatched_snapshot_type_raises(
    repository: BankAccountRepository,
    snapshot_store: AsyncMock,
) -> None:
    snapshot_store.load.return_value = Snapshot(
        stream_id='BankAccount-acc-1',
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
