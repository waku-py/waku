from __future__ import annotations

from typing import Any

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import SnapshotMigrationChainError
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain


class AddBalanceFieldMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'balance': 0}


class RenameNameToOwnerMigration(ISnapshotMigration):
    from_version = 2
    to_version = 3

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        new_state = {**state}
        new_state['owner'] = new_state.pop('name')
        return new_state


class InPlaceMutatingMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        state['migrated'] = True
        return state


def test_migration_chain_does_not_mutate_input() -> None:
    chain = SnapshotMigrationChain([InPlaceMutatingMigration()])
    original = {'name': 'Alice'}

    result_state, _ = chain.migrate(original, from_version=1)

    assert original == {'name': 'Alice'}
    assert result_state == {'name': 'Alice', 'migrated': True}


def test_migrate_applies_single_migration() -> None:
    chain = SnapshotMigrationChain([AddBalanceFieldMigration()])

    result_state, result_version = chain.migrate({'name': 'Alice'}, from_version=1)

    assert result_state == {'name': 'Alice', 'balance': 0}
    assert result_version == 2


def test_migrate_applies_chain_of_migrations() -> None:
    chain = SnapshotMigrationChain([AddBalanceFieldMigration(), RenameNameToOwnerMigration()])

    result_state, result_version = chain.migrate({'name': 'Alice'}, from_version=1)

    assert result_state == {'owner': 'Alice', 'balance': 0}
    assert result_version == 3


def test_migrate_returns_unchanged_when_no_migrations() -> None:
    chain = SnapshotMigrationChain([])
    original_state = {'name': 'Alice'}

    result_state, result_version = chain.migrate(original_state, from_version=1)

    assert result_state == {'name': 'Alice'}
    assert result_version == 1


def test_migrate_returns_unchanged_when_already_past_chain() -> None:
    chain = SnapshotMigrationChain([AddBalanceFieldMigration()])
    original_state = {'name': 'Alice', 'balance': 100, 'owner': 'Alice'}

    result_state, result_version = chain.migrate(original_state, from_version=3)

    assert result_state == original_state
    assert result_version == 3


def _passthrough_migration(*, from_v: int, to_v: int) -> ISnapshotMigration:
    class _Passthrough(ISnapshotMigration):
        from_version = from_v
        to_version = to_v

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    return _Passthrough()


@pytest.mark.parametrize(
    ('migrations', 'match'),
    [
        pytest.param(
            [_passthrough_migration(from_v=1, to_v=2), _passthrough_migration(from_v=1, to_v=2)],
            'Duplicate snapshot migration at from_version 1',
            id='duplicate_from_version',
        ),
        pytest.param(
            [_passthrough_migration(from_v=0, to_v=1)],
            'Invalid from_version 0',
            id='invalid_from_version',
        ),
        pytest.param(
            [_passthrough_migration(from_v=2, to_v=2)],
            'to_version 2 must be > from_version 2',
            id='to_version_not_greater_than_from',
        ),
        pytest.param(
            [_passthrough_migration(from_v=1, to_v=2), _passthrough_migration(from_v=3, to_v=4)],
            'Gap in snapshot migration chain',
            id='gap_in_migration_sequence',
        ),
    ],
)
def test_chain_rejects_invalid_migration_set(migrations: list[ISnapshotMigration], match: str) -> None:
    with pytest.raises(SnapshotMigrationChainError, match=match):
        SnapshotMigrationChain(migrations)


def test_migrations_property_returns_empty_tuple_for_empty_chain() -> None:
    chain = SnapshotMigrationChain([])

    assert chain.migrations == ()


def test_migrations_property_returns_sorted_migrations() -> None:
    rename = RenameNameToOwnerMigration()
    add_balance = AddBalanceFieldMigration()
    chain = SnapshotMigrationChain([rename, add_balance])

    assert chain.migrations == (add_balance, rename)
