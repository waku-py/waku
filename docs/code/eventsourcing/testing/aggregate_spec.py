from app.aggregate import BankAccount
from app.events import AccountOpened, MoneyDeposited
from waku.eventsourcing.testing import AggregateSpec


def test_deposit_produces_event() -> None:
    (
        AggregateSpec
        .for_(BankAccount)
        .given([AccountOpened(account_id='acc-1', owner='dex')])
        .when(lambda acc: acc.deposit('acc-1', 500))
        .then([MoneyDeposited(account_id='acc-1', amount=500)])
    )


def test_withdraw_insufficient_funds_raises() -> None:
    (
        AggregateSpec
        .for_(BankAccount)
        .given([AccountOpened(account_id='acc-1', owner='dex')])
        .when(lambda acc: acc.withdraw('acc-1', 9999))
        .then_raises(ValueError, match='Insufficient funds')
    )


def test_balance_after_deposits() -> None:
    (
        AggregateSpec
        .for_(BankAccount)
        .given([
            AccountOpened(account_id='acc-1', owner='dex'),
            MoneyDeposited(account_id='acc-1', amount=500),
            MoneyDeposited(account_id='acc-1', amount=300),
        ])
        .then_state(lambda acc: acc.balance == 800)
    )


def test_no_op_produces_no_events() -> None:
    (
        AggregateSpec
        .for_(BankAccount)
        .given([AccountOpened(account_id='acc-1', owner='dex')])
        .when(lambda acc: acc.noop())
        .then_no_events()
    )
