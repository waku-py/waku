from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate, IDecider
from waku.eventsourcing.repository import EventSourcedRepository
from waku.messages import IEvent


@dataclass(frozen=True)
class AccountOpened(IEvent):
    name: str


@dataclass(frozen=True)
class MoneyDeposited(IEvent):
    amount: int


@dataclass(frozen=True)
class AccountState:
    name: str
    balance: int


class BankAccount(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.name: str = ''
        self.balance: int = 0

    def open(self, name: str) -> None:
        self._raise_event(AccountOpened(name=name))

    def deposit(self, amount: int) -> None:
        self._raise_event(MoneyDeposited(amount=amount))

    def _apply(self, event: IEvent) -> None:
        match event:
            case AccountOpened(name=name):
                self.name = name
            case MoneyDeposited(amount=amount):
                self.balance += amount


@dataclass(frozen=True)
class NoteCreated(IEvent):
    title: str


@dataclass(frozen=True)
class NoteEdited(IEvent):
    content: str


class Note(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ''
        self.content: str = ''

    def create(self, title: str) -> None:
        self._raise_event(NoteCreated(title=title))

    def edit(self, content: str) -> None:
        self._raise_event(NoteEdited(content=content))

    def _apply(self, event: IEvent) -> None:
        match event:
            case NoteCreated(title=title):
                self.title = title
            case NoteEdited(content=content):
                self.content = content


class NoteRepository(EventSourcedRepository[Note]):
    pass


@dataclass(frozen=True)
class CounterState:
    value: int = 0


@dataclass(frozen=True)
class Increment:
    amount: int = 1


@dataclass(frozen=True)
class Incremented(IEvent):
    amount: int


class CounterDecider(IDecider[CounterState, Increment, Incremented]):
    @override
    def initial_state(self) -> CounterState:
        return CounterState()

    @override
    def decide(self, command: Increment, state: CounterState) -> list[Incremented]:
        if command.amount <= 0:
            msg = 'Amount must be positive'
            raise ValueError(msg)
        return [Incremented(amount=command.amount)]

    @override
    def evolve(self, state: CounterState, event: Incremented) -> CounterState:
        return CounterState(value=state.value + event.amount)


@dataclass(frozen=True)
class AccountClosed(IEvent):
    pass


@dataclass(frozen=True)
class NotCreated:
    pass


@dataclass(frozen=True)
class Active:
    owner: str
    balance: int = 0


@dataclass(frozen=True)
class Closed:
    owner: str


BankAccountState = NotCreated | Active | Closed


@dataclass(frozen=True)
class OpenAccount:
    owner: str


@dataclass(frozen=True)
class Deposit:
    amount: int


@dataclass(frozen=True)
class CloseAccount:
    pass


BankCommand = OpenAccount | Deposit | CloseAccount
BankEvent = AccountOpened | MoneyDeposited | AccountClosed


class BankAccountDecider(IDecider[BankAccountState, BankCommand, BankEvent]):
    @override
    def initial_state(self) -> BankAccountState:
        return NotCreated()

    @override
    def decide(self, command: BankCommand, state: BankAccountState) -> list[BankEvent]:
        match (command, state):
            case (OpenAccount(owner=owner), NotCreated()):
                return [AccountOpened(name=owner)]
            case (Deposit(amount=amount), Active()):
                return [MoneyDeposited(amount=amount)]
            case (CloseAccount(), Active()):
                return [AccountClosed()]
            case _:
                msg = f'Cannot {type(command).__name__} while {type(state).__name__}'
                raise ValueError(msg)

    @override
    def evolve(self, state: BankAccountState, event: BankEvent) -> BankAccountState:
        match (event, state):
            case (AccountOpened(name=name), NotCreated()):
                return Active(owner=name)
            case (MoneyDeposited(amount=amount), Active(owner=owner, balance=balance)):
                return Active(owner=owner, balance=balance + amount)
            case (AccountClosed(), Active(owner=owner)):
                return Closed(owner=owner)
            case _:
                return state
