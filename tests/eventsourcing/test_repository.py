from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, ConcurrencyConflictError
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.store.in_memory import InMemoryEventStore


@dataclass(frozen=True)
class AccountOpened(INotification):
    name: str


@dataclass(frozen=True)
class MoneyDeposited(INotification):
    amount: int


class BankAccount(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.name: str = ''
        self.balance: int = 0

    def open(self, name: str) -> None:
        self._raise_event(AccountOpened(name=name))

    def deposit(self, amount: int) -> None:
        self._raise_event(MoneyDeposited(amount=amount))

    def _apply(self, event: INotification) -> None:
        match event:
            case AccountOpened(name=name):
                self.name = name
            case MoneyDeposited(amount=amount):
                self.balance += amount


class BankAccountRepository(EventSourcedRepository[BankAccount]):
    aggregate_type_name = 'BankAccount'

    @override
    def create_aggregate(self) -> BankAccount:
        return BankAccount()

    @override
    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate('BankAccount', aggregate_id)


# --- Fixtures ---


@pytest.fixture
def store() -> InMemoryEventStore:
    return InMemoryEventStore()


@pytest.fixture
def repository(store: InMemoryEventStore) -> BankAccountRepository:
    return BankAccountRepository(store)


# --- Tests ---


async def test_save_new_aggregate_persists_events_and_returns_version(
    store: InMemoryEventStore,
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)

    new_version, events = await repository.save('acc-1', account)

    assert new_version == 1
    assert len(events) == 2
    stream_id = StreamId.for_aggregate('BankAccount', 'acc-1')
    stored = await store.read_stream(stream_id)
    assert len(stored) == 2
    assert stored[0].data == AccountOpened(name='Alice')
    assert stored[1].data == MoneyDeposited(amount=100)


async def test_load_restores_aggregate_state_from_stored_events(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Bob')
    account.deposit(250)
    await repository.save('acc-2', account)

    loaded = await repository.load('acc-2')

    assert loaded.name == 'Bob'
    assert loaded.balance == 250


async def test_load_sets_correct_version_on_aggregate(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Carol')
    account.deposit(50)
    account.deposit(75)
    await repository.save('acc-3', account)

    loaded = await repository.load('acc-3')

    assert loaded.version == 2


async def test_load_nonexistent_aggregate_raises_aggregate_not_found_error(
    repository: BankAccountRepository,
) -> None:
    with pytest.raises(AggregateNotFoundError) as exc_info:
        await repository.load('does-not-exist')

    assert exc_info.value.aggregate_id == 'does-not-exist'
    assert 'BankAccount' in exc_info.value.aggregate_type


async def test_save_then_load_roundtrip_produces_equivalent_state(
    repository: BankAccountRepository,
) -> None:
    original = BankAccount()
    original.open('Dana')
    original.deposit(100)
    original.deposit(200)
    await repository.save('acc-4', original)

    restored = await repository.load('acc-4')

    assert restored.name == original.name
    assert restored.balance == original.balance


async def test_save_collects_and_returns_events(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Eve')
    account.deposit(500)

    _, events = await repository.save('acc-5', account)

    assert events == [AccountOpened(name='Eve'), MoneyDeposited(amount=500)]
    assert account.collect_events() == []


async def test_save_with_no_events_returns_current_version(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Frank')
    new_version, _ = await repository.save('acc-6', account)

    account_reloaded = await repository.load('acc-6')
    result_version, result_events = await repository.save('acc-6', account_reloaded)

    assert result_version == new_version
    assert result_events == []


async def test_concurrent_save_on_same_aggregate_raises_concurrency_conflict_error(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Grace')
    await repository.save('acc-7', account)

    reader_a = await repository.load('acc-7')
    reader_b = await repository.load('acc-7')

    reader_a.deposit(100)
    await repository.save('acc-7', reader_a)

    reader_b.deposit(200)
    with pytest.raises(ConcurrencyConflictError):
        await repository.save('acc-7', reader_b)
