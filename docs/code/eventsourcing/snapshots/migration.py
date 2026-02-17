from typing import Any

from waku.eventsourcing.snapshot import ISnapshotMigration, Snapshot, SnapshotEventSourcedRepository

from app.aggregate import BankAccount


class AddEmailField(ISnapshotMigration):
    from_version = 1
    to_version = 2

    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'email': ''}


class RenameOwnerToName(ISnapshotMigration):
    from_version = 2
    to_version = 3

    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        owner = state.pop('owner', '')
        return {**state, 'name': owner}


class BankAccountSnapshotRepository(SnapshotEventSourcedRepository[BankAccount]):
    def _snapshot_state(self, aggregate: BankAccount) -> object:
        return {'name': aggregate.owner, 'email': '', 'balance': aggregate.balance}

    def _restore_from_snapshot(self, snapshot: Snapshot) -> BankAccount:
        account = BankAccount()
        account.owner = snapshot.state['name']
        account.balance = snapshot.state['balance']
        return account
