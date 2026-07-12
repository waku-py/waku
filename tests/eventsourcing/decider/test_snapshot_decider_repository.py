from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from typing_extensions import TypeAliasType, override

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture

    from waku.eventsourcing.contracts.aggregate import IDecider

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.decider.repository import SnapshotDeciderRepository
from waku.eventsourcing.exceptions import EventSourcingConfigError, SnapshotTypeMismatchError
from waku.eventsourcing.serialization.json import JsonSnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig, SnapshotConfigRegistry
from waku.eventsourcing.snapshot.strategy import EventCountStrategy
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import ISnapshotStore

from tests.eventsourcing.domain import (
    AccountClosed,
    AccountOpened,
    Active,
    BankAccountDecider,
    BankAccountState,
    BankCommand,
    BankEvent,
    Closed,
    CounterDecider,
    CounterState,
    Increment,
    Incremented,
    MoneyDeposited,
    NotCreated,
)


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
    caplog: pytest.LogCaptureFixture,
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
    assert 'Failed to save snapshot' in caplog.text


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


# --- union state variant family ---


class BankAccountSnapshotRepository(SnapshotDeciderRepository[BankAccountState, BankCommand, BankEvent]):
    aggregate_name = 'BankAccount'


def _bank_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(AccountOpened)
    registry.register(MoneyDeposited)
    registry.register(AccountClosed)
    return registry


def _bank_repository(
    snapshot_store: ISnapshotStore,
    *,
    threshold: int = 3,
    schema_version: int = 1,
    migration_chain: SnapshotMigrationChain | None = None,
) -> BankAccountSnapshotRepository:
    config = SnapshotConfig(
        strategy=EventCountStrategy(threshold=threshold),
        schema_version=schema_version,
        migration_chain=migration_chain or SnapshotMigrationChain(()),
    )
    return BankAccountSnapshotRepository(
        decider=BankAccountDecider(),
        event_store=InMemoryEventStore(registry=_bank_registry()),
        snapshot_store=snapshot_store,
        snapshot_config_registry=SnapshotConfigRegistry({'BankAccount': config}),
        state_serializer=JsonSnapshotStateSerializer(),
    )


async def test_union_state_round_trip_restores_concrete_variant() -> None:
    repo = _bank_repository(InMemorySnapshotStore())

    await repo.save(
        'acc-1',
        [AccountOpened(name='dex'), MoneyDeposited(amount=60), MoneyDeposited(amount=40)],
        expected_version=-1,
    )

    state, version = await repo.load('acc-1')

    assert state == Active(owner='dex', balance=100)
    assert version == 2


async def test_variant_switch_writes_concrete_discriminator_per_snapshot() -> None:
    snapshot_store = InMemorySnapshotStore()
    repo = _bank_repository(snapshot_store, threshold=1)
    stream_id = StreamId.for_aggregate('BankAccount', 'acc-1')

    await repo.save('acc-1', [AccountOpened(name='dex')], expected_version=-1)
    active_snapshot = await snapshot_store.load(stream_id)
    active_state, _ = await repo.load('acc-1')

    assert active_snapshot is not None
    assert active_snapshot.state_type == 'Active'
    assert active_state == Active(owner='dex')

    await repo.save('acc-1', [AccountClosed()], expected_version=0)
    closed_snapshot = await snapshot_store.load(stream_id)
    closed_state, version = await repo.load('acc-1')

    assert closed_snapshot is not None
    assert closed_snapshot.state_type == 'Closed'
    assert closed_state == Closed(owner='dex')
    assert version == 1


async def test_snapshot_save_stamps_concrete_variant_name(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    repo = _bank_repository(snapshot_store, threshold=1)

    await repo.save('acc-1', [AccountOpened(name='dex')], expected_version=-1, current_state=Active(owner='dex'))
    await repo.save('acc-1', [AccountClosed()], expected_version=0, current_state=Closed(owner='dex'))

    first: Snapshot = snapshot_store.save.call_args_list[0][0][0]
    second: Snapshot = snapshot_store.save.call_args_list[1][0][0]
    assert first.state_type == 'Active'
    assert second.state_type == 'Closed'


async def test_union_repo_rejects_snapshot_with_foreign_state_type(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'owner': 'dex'},
        version=0,
        state_type='Bogus',
    )
    repo = _bank_repository(snapshot_store)

    with pytest.raises(SnapshotTypeMismatchError, match='Bogus'):
        await repo.load('acc-1')


class AddBalanceFieldMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'balance': 0}


async def test_migration_applies_to_non_initial_variant_snapshot(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    repo = _bank_repository(
        snapshot_store,
        threshold=100,
        schema_version=2,
        migration_chain=SnapshotMigrationChain([AddBalanceFieldMigration()]),
    )

    await repo.save('acc-1', [AccountOpened(name='dex'), MoneyDeposited(amount=50)], expected_version=-1)

    snapshot_store.load.return_value = Snapshot(
        stream_id=StreamId.for_aggregate('BankAccount', 'acc-1'),
        state={'owner': 'dex'},
        version=1,
        state_type='Active',
        schema_version=1,
    )

    state, version = await repo.load('acc-1')

    assert state == Active(owner='dex', balance=0)
    assert version == 1


async def test_save_with_state_outside_declared_family_raises(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    repo = _bank_repository(snapshot_store, threshold=1)
    foreign_state = cast('BankAccountState', CounterState())

    with pytest.raises(EventSourcingConfigError, match='CounterState'):
        await repo.save('acc-1', [AccountOpened(name='dex')], expected_version=-1, current_state=foreign_state)

    snapshot_store.save.assert_not_called()


class PinnedUnionSnapshotRepository(SnapshotDeciderRepository[BankAccountState, BankCommand, BankEvent]):
    aggregate_name = 'PinnedBankAccount'
    snapshot_state_type = 'BankAccountState'


def test_union_state_with_scalar_pin_raises_config_error(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)

    with pytest.raises(EventSourcingConfigError, match='snapshot_state_type'):
        PinnedUnionSnapshotRepository(
            decider=BankAccountDecider(),
            event_store=InMemoryEventStore(registry=_bank_registry()),
            snapshot_store=snapshot_store,
            snapshot_config_registry=SnapshotConfigRegistry({
                'PinnedBankAccount': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
            }),
            state_serializer=JsonSnapshotStateSerializer(),
        )


AliasedBankAccountState = TypeAliasType('AliasedBankAccountState', BankAccountState)


class AliasedBankAccountSnapshotRepository(
    SnapshotDeciderRepository[AliasedBankAccountState, BankCommand, BankEvent],
):
    aggregate_name = 'AliasedBankAccount'


async def test_alias_wrapped_union_resolves_variant_family(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    snapshot_store.load.return_value = None
    repo = AliasedBankAccountSnapshotRepository(
        decider=BankAccountDecider(),
        event_store=InMemoryEventStore(registry=_bank_registry()),
        snapshot_store=snapshot_store,
        snapshot_config_registry=SnapshotConfigRegistry({
            'AliasedBankAccount': SnapshotConfig(strategy=EventCountStrategy(threshold=1)),
        }),
        state_serializer=JsonSnapshotStateSerializer(),
    )

    await repo.save('acc-1', [AccountOpened(name='dex')], expected_version=-1, current_state=Active(owner='dex'))

    saved: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved.state_type == 'Active'


class UntypedCounterSnapshotRepository(SnapshotDeciderRepository):  # type: ignore[type-arg]
    aggregate_name = 'UntypedCounter'


async def test_unparametrized_repository_falls_back_to_initial_state_type() -> None:
    snapshot_store = InMemorySnapshotStore()
    registry = EventTypeRegistry()
    registry.register(Incremented)
    repo = UntypedCounterSnapshotRepository(
        decider=CounterDecider(),
        event_store=InMemoryEventStore(registry=registry),
        snapshot_store=snapshot_store,
        snapshot_config_registry=SnapshotConfigRegistry({
            'UntypedCounter': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
        }),
        state_serializer=JsonSnapshotStateSerializer(),
    )

    await repo.save('c-1', [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)], expected_version=-1)

    saved = await snapshot_store.load(StreamId.for_aggregate('UntypedCounter', 'c-1'))
    assert saved is not None
    assert saved.state_type == 'CounterState'

    state, _ = await repo.load('c-1')
    assert state == CounterState(value=6)


class PinnedCounterSnapshotRepository(SnapshotDeciderRepository[CounterState, Increment, Incremented]):
    aggregate_name = 'PinnedCounter'
    snapshot_state_type = 'Counter'


async def test_snapshot_state_type_pin_relabels_monomorphic_family() -> None:
    snapshot_store = InMemorySnapshotStore()
    registry = EventTypeRegistry()
    registry.register(Incremented)
    repo = PinnedCounterSnapshotRepository(
        decider=CounterDecider(),
        event_store=InMemoryEventStore(registry=registry),
        snapshot_store=snapshot_store,
        snapshot_config_registry=SnapshotConfigRegistry({
            'PinnedCounter': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
        }),
        state_serializer=JsonSnapshotStateSerializer(),
    )

    await repo.save('c-1', [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)], expected_version=-1)

    saved = await snapshot_store.load(StreamId.for_aggregate('PinnedCounter', 'c-1'))
    assert saved is not None
    assert saved.state_type == 'Counter'

    state, _ = await repo.load('c-1')
    assert state == CounterState(value=6)


_FirstDuplicate = type('DuplicateState', (), {})
_SecondDuplicate = type('DuplicateState', (), {})


class DuplicateNameSnapshotRepository(
    SnapshotDeciderRepository[_FirstDuplicate | _SecondDuplicate, BankCommand, BankEvent],  # type: ignore[valid-type]
):
    aggregate_name = 'Duplicate'


def test_union_with_duplicate_variant_names_raises_config_error(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)

    with pytest.raises(EventSourcingConfigError, match='DuplicateState'):
        DuplicateNameSnapshotRepository(
            decider=BankAccountDecider(),
            event_store=InMemoryEventStore(registry=_bank_registry()),
            snapshot_store=snapshot_store,
            snapshot_config_registry=SnapshotConfigRegistry({
                'Duplicate': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
            }),
            state_serializer=JsonSnapshotStateSerializer(),
        )


class NonClassMemberSnapshotRepository(
    SnapshotDeciderRepository[NotCreated | list[int], BankCommand, BankEvent],
):
    aggregate_name = 'NonClassMember'


def test_union_with_non_class_member_raises_config_error(mocker: MockerFixture) -> None:
    snapshot_store: AsyncMock = mocker.AsyncMock(spec=ISnapshotStore)
    decider = cast('IDecider[NotCreated | list[int], BankCommand, BankEvent]', BankAccountDecider())

    with pytest.raises(EventSourcingConfigError, match='list'):
        NonClassMemberSnapshotRepository(
            decider=decider,
            event_store=InMemoryEventStore(registry=_bank_registry()),
            snapshot_store=snapshot_store,
            snapshot_config_registry=SnapshotConfigRegistry({
                'NonClassMember': SnapshotConfig(strategy=EventCountStrategy(threshold=3)),
            }),
            state_serializer=JsonSnapshotStateSerializer(),
        )
