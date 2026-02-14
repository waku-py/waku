from dataclasses import dataclass

from waku.cqrs import INotification


@dataclass(frozen=True, kw_only=True)
class AccountOpened(INotification):
    account_id: str
    owner: str


@dataclass(frozen=True, kw_only=True)
class MoneyDeposited(INotification):
    account_id: str
    amount: int


@dataclass(frozen=True, kw_only=True)
class MoneyWithdrawn(INotification):
    account_id: str
    amount: int
