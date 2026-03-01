from waku.eventsourcing import DeciderRepository

from app.decider import BankCommand, BankEvent
from app.state import BankAccountState


class BankAccountDeciderRepository(DeciderRepository[BankAccountState, BankCommand, BankEvent]):
    pass
