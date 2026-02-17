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


def test_chain_rejects_duplicate_from_version() -> None:
    class AnotherV1Migration(ISnapshotMigration):
        from_version = 1
        to_version = 2

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    with pytest.raises(SnapshotMigrationChainError, match='Duplicate snapshot migration at from_version 1'):
        SnapshotMigrationChain([AddBalanceFieldMigration(), AnotherV1Migration()])


def test_chain_rejects_invalid_from_version() -> None:
    class ZeroVersionMigration(ISnapshotMigration):
        from_version = 0
        to_version = 1

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    with pytest.raises(SnapshotMigrationChainError, match='Invalid from_version 0'):
        SnapshotMigrationChain([ZeroVersionMigration()])


def test_chain_rejects_to_version_not_greater_than_from() -> None:
    class BadMigration(ISnapshotMigration):
        from_version = 2
        to_version = 2

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    with pytest.raises(SnapshotMigrationChainError, match='to_version 2 must be > from_version 2'):
        SnapshotMigrationChain([BadMigration()])


def test_chain_rejects_gap_in_migration_sequence() -> None:
    class V1ToV2(ISnapshotMigration):
        from_version = 1
        to_version = 2

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    class V3ToV4(ISnapshotMigration):
        from_version = 3
        to_version = 4

        @override
        def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
            return state

    with pytest.raises(SnapshotMigrationChainError, match='Gap in snapshot migration chain'):
        SnapshotMigrationChain([V1ToV2(), V3ToV4()])
