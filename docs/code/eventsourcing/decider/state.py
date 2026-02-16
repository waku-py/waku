from dataclasses import dataclass


@dataclass(frozen=True)
class BankAccountState:
    owner: str = ''
    balance: int = 0
