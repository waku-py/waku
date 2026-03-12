from waku.messaging import IEvent
from waku.eventsourcing import SnapshotDeciderRepository

from app.decider import BankCommand
from app.state import BankAccountState


class BankAccountSnapshotRepository(SnapshotDeciderRepository[BankAccountState, BankCommand, IEvent]):
    aggregate_name = 'BankAccount'
