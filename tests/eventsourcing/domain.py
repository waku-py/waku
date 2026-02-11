from __future__ import annotations

from dataclasses import dataclass

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.repository import EventSourcedRepository

# --- Bank Account ---


@dataclass(frozen=True)
class AccountOpened(INotification):
    name: str


@dataclass(frozen=True)
class MoneyDeposited(INotification):
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

    def _apply(self, event: INotification) -> None:
        match event:
            case AccountOpened(name=name):
                self.name = name
            case MoneyDeposited(amount=amount):
                self.balance += amount


# --- Note ---


@dataclass(frozen=True)
class NoteCreated(INotification):
    title: str


@dataclass(frozen=True)
class NoteEdited(INotification):
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

    def _apply(self, event: INotification) -> None:
        match event:
            case NoteCreated(title=title):
                self.title = title
            case NoteEdited(content=content):
                self.content = content


class NoteRepository(EventSourcedRepository[Note]):
    pass
