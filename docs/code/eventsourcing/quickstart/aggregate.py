from typing_extensions import override

from waku.messaging import IEvent
from waku.eventsourcing import EventSourcedAggregate

from app.events import AccountOpened, MoneyDeposited, MoneyWithdrawn


class BankAccount(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.account_id: str = ''
        self.owner: str = ''
        self.balance: int = 0

    def open(self, account_id: str, owner: str) -> None:
        self._raise_event(AccountOpened(account_id=account_id, owner=owner))

    def deposit(self, account_id: str, amount: int) -> None:
        if amount <= 0:
            msg = 'Deposit amount must be positive'
            raise ValueError(msg)
        self._raise_event(MoneyDeposited(account_id=account_id, amount=amount))

    def withdraw(self, account_id: str, amount: int) -> None:
        if amount > self.balance:
            msg = f'Insufficient funds: balance={self.balance}, requested={amount}'
            raise ValueError(msg)
        self._raise_event(MoneyWithdrawn(account_id=account_id, amount=amount))

    @override
    def _apply(self, event: IEvent) -> None:
        match event:
            case AccountOpened(account_id=account_id, owner=owner):
                self.account_id = account_id
                self.owner = owner
            case MoneyDeposited(amount=amount):
                self.balance += amount
            case MoneyWithdrawn(amount=amount):
                self.balance -= amount
