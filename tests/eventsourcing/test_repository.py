from __future__ import annotations

import pytest

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, ConcurrencyConflictError, StreamTooLargeError
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.domain import AccountOpened, BankAccount, MoneyDeposited


class BankAccountRepository(EventSourcedRepository[BankAccount]):
    pass


class LimitedBankAccountRepository(EventSourcedRepository[BankAccount]):
    max_stream_length = 3


@pytest.fixture
def store() -> InMemoryEventStore:
    registry = EventTypeRegistry()
    registry.register(AccountOpened)
    registry.register(MoneyDeposited)
    return InMemoryEventStore(registry=registry)


@pytest.fixture
def repository(store: InMemoryEventStore) -> BankAccountRepository:
    return BankAccountRepository(store)


@pytest.fixture
def limited_repository(store: InMemoryEventStore) -> LimitedBankAccountRepository:
    return LimitedBankAccountRepository(store)


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
    with pytest.raises(AggregateNotFoundError):
        await repository.load('nonexistent')


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


async def test_load_raises_stream_too_large_error_when_stream_exceeds_limit(
    limited_repository: LimitedBankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)
    account.deposit(200)
    account.deposit(300)
    await limited_repository.save('acc-big', account)

    with pytest.raises(StreamTooLargeError) as exc_info:
        await limited_repository.load('acc-big')

    assert exc_info.value.stream_id == StreamId.for_aggregate('BankAccount', 'acc-big')
    assert exc_info.value.max_length == 3


async def test_load_succeeds_when_stream_within_limit(
    limited_repository: LimitedBankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Bob')
    account.deposit(100)
    account.deposit(200)
    await limited_repository.save('acc-ok', account)

    loaded = await limited_repository.load('acc-ok')

    assert loaded.name == 'Bob'
    assert loaded.balance == 300


async def test_load_with_no_max_stream_length_loads_any_size(
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Carol')
    for _i in range(20):
        account.deposit(10)
    await repository.save('acc-large', account)

    loaded = await repository.load('acc-large')

    assert loaded.name == 'Carol'
    assert loaded.balance == 200


async def test_save_with_idempotency_key_generates_indexed_keys(
    store: InMemoryEventStore,
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Alice')
    account.deposit(100)

    await repository.save('acc-idem-1', account, idempotency_key='cmd-123')

    stream_id = StreamId.for_aggregate('BankAccount', 'acc-idem-1')
    stored = await store.read_stream(stream_id)
    assert [e.idempotency_key for e in stored] == ['cmd-123:0', 'cmd-123:1']


async def test_save_with_same_idempotency_key_is_idempotent(
    store: InMemoryEventStore,
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Bob')
    account.deposit(200)
    first_version, _ = await repository.save('acc-idem-2', account, idempotency_key='cmd-456')

    retry_account = BankAccount()
    retry_account.open('Bob')
    retry_account.deposit(200)
    second_version, _ = await repository.save('acc-idem-2', retry_account, idempotency_key='cmd-456')

    assert second_version == first_version
    stream_id = StreamId.for_aggregate('BankAccount', 'acc-idem-2')
    stored = await store.read_stream(stream_id)
    assert len(stored) == 2
    loaded = await repository.load('acc-idem-2')
    assert loaded.name == 'Bob'
    assert loaded.balance == 200


async def test_save_without_idempotency_key_generates_unique_uuid_keys(
    store: InMemoryEventStore,
    repository: BankAccountRepository,
) -> None:
    account = BankAccount()
    account.open('Carol')
    account.deposit(50)

    await repository.save('acc-idem-3', account)

    stream_id = StreamId.for_aggregate('BankAccount', 'acc-idem-3')
    stored = await store.read_stream(stream_id)
    keys = [e.idempotency_key for e in stored]
    assert keys[0] != keys[1]
    for key in keys:
        assert len(key) == 36
        assert key.count('-') == 4
