from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.state import BankAccountState
from app.events import AccountOpened, MoneyDeposited

if TYPE_CHECKING:
    from waku.cqrs import INotification


@dataclass(frozen=True, kw_only=True)
class OpenAccount:
    account_id: str
    owner: str


@dataclass(frozen=True, kw_only=True)
class DepositMoney:
    account_id: str
    amount: int


BankCommand = OpenAccount | DepositMoney
BankEvent = AccountOpened | MoneyDeposited


class BankAccountDecider:
    def initial_state(self) -> BankAccountState:
        return BankAccountState()

    def decide(self, command: BankCommand, state: BankAccountState) -> list[BankEvent]:
        match command:
            case OpenAccount(account_id=aid, owner=owner):
                return [AccountOpened(account_id=aid, owner=owner)]
            case DepositMoney(account_id=aid, amount=amount):
                if amount <= 0:
                    msg = 'Deposit amount must be positive'
                    raise ValueError(msg)
                return [MoneyDeposited(account_id=aid, amount=amount)]

    def evolve(self, state: BankAccountState, event: INotification) -> BankAccountState:
        match event:
            case AccountOpened(owner=owner):
                return replace(state, owner=owner)
            case MoneyDeposited(amount=amount):
                return replace(state, balance=state.balance + amount)
        return state
