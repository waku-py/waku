from waku.eventsourcing.snapshot import Snapshot, SnapshotEventSourcedRepository

from app.aggregate import BankAccount


class BankAccountSnapshotRepository(SnapshotEventSourcedRepository[BankAccount]):
    def _snapshot_state(self, aggregate: BankAccount) -> object:
        return {'owner': aggregate.owner, 'balance': aggregate.balance}

    def _restore_from_snapshot(self, snapshot: Snapshot) -> BankAccount:
        account = BankAccount()
        account.owner = snapshot.state['owner']
        account.balance = snapshot.state['balance']
        return account
