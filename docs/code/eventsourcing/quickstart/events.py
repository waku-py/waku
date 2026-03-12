from dataclasses import dataclass

from waku.messaging import IEvent


@dataclass(frozen=True, kw_only=True)
class AccountOpened(IEvent):
    account_id: str
    owner: str


@dataclass(frozen=True, kw_only=True)
class MoneyDeposited(IEvent):
    account_id: str
    amount: int


@dataclass(frozen=True, kw_only=True)
class MoneyWithdrawn(IEvent):
    account_id: str
    amount: int
