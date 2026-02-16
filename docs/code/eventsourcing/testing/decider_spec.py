from app.decider import (
    BankAccountDecider,
    DepositMoney,
    OpenAccount,
)
from app.events import AccountOpened, MoneyDeposited
from waku.eventsourcing.testing import DeciderSpec


def test_open_account() -> None:
    decider = BankAccountDecider()

    (
        DeciderSpec
        .for_(decider)
        .given([])
        .when(OpenAccount(account_id='acc-1', owner='dex'))
        .then([AccountOpened(account_id='acc-1', owner='dex')])
    )


def test_deposit_updates_balance() -> None:
    decider = BankAccountDecider()

    (
        DeciderSpec
        .for_(decider)
        .given([AccountOpened(account_id='acc-1', owner='dex')])
        .when(DepositMoney(account_id='acc-1', amount=500))
        .then([MoneyDeposited(account_id='acc-1', amount=500)])
    )


def test_deposit_negative_raises() -> None:
    decider = BankAccountDecider()

    (
        DeciderSpec
        .for_(decider)
        .given([AccountOpened(account_id='acc-1', owner='dex')])
        .when(DepositMoney(account_id='acc-1', amount=-10))
        .then_raises(ValueError, match='Deposit amount must be positive')
    )


def test_state_after_events() -> None:
    decider = BankAccountDecider()

    (
        DeciderSpec
        .for_(decider)
        .given([
            AccountOpened(account_id='acc-1', owner='dex'),
            MoneyDeposited(account_id='acc-1', amount=500),
        ])
        .when(DepositMoney(account_id='acc-1', amount=300))
        .then_state(lambda s: s.balance == 800)
    )
