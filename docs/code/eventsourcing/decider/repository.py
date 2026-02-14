from waku.cqrs import INotification
from waku.eventsourcing import DeciderRepository

from app.decider import BankCommand
from app.state import BankAccountState


class BankAccountDeciderRepository(DeciderRepository[BankAccountState, BankCommand, INotification]):
    aggregate_name = 'BankAccount'
