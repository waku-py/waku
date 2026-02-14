from app.aggregate import BankAccount
from app.events import AccountOpened, MoneyDeposited


def test_aggregate_opens_account() -> None:
    account = BankAccount()
    account.open('acc-1', 'dex')

    events = account.collect_events()
    assert len(events) == 1
    assert isinstance(events[0], AccountOpened)
    assert events[0].owner == 'dex'


def test_aggregate_deposits_money() -> None:
    account = BankAccount()
    account.load_from_history(
        [AccountOpened(account_id='acc-1', owner='dex')],
        version=0,
    )

    account.deposit('acc-1', 500)

    events = account.collect_events()
    assert len(events) == 1
    assert isinstance(events[0], MoneyDeposited)
    assert account.balance == 500
