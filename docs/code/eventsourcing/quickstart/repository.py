from waku.eventsourcing import EventSourcedRepository

from app.aggregate import BankAccount


class BankAccountRepository(EventSourcedRepository[BankAccount]):
    pass
