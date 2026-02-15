from waku.cqrs import INotification
from waku.eventsourcing import SnapshotDeciderRepository

from app.decider import BankCommand
from app.state import BankAccountState


class BankAccountSnapshotRepository(SnapshotDeciderRepository[BankAccountState, BankCommand, INotification]):
    aggregate_name = 'BankAccount'
