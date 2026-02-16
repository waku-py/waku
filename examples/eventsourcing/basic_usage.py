"""Event Sourcing with In-Memory Store — current API as of 2026-02-09.

Shows the full registration and usage flow:
1. Define domain events (frozen dataclasses implementing INotification)
2. Define an aggregate (EventSourcedAggregate subclass)
3. Define a repository (EventSourcedRepository subclass)
4. Define a command + handler (EventSourcedCommandHandler)
5. Wire modules: EventSourcingModule + EventSourcingExtension
6. Run a command through the mediator
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.cqrs import (
    EventHandler,
    IMediator,
    INotification,
    MediatorExtension,
    MediatorModule,
    Request,
    Response,
)
from waku.eventsourcing import (
    EventSourcedAggregate,
    EventSourcedCommandHandler,
    EventSourcedRepository,
    EventSourcingConfig,
    EventSourcingExtension,
    EventSourcingModule,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Domain Events ──────────────────────────────────────────────────


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


# ── Aggregate ──────────────────────────────────────────────────────


class BankAccount(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
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
    def _apply(self, event: INotification) -> None:
        match event:
            case AccountOpened(owner=owner):
                self.owner = owner
            case MoneyDeposited(amount=amount):
                self.balance += amount
            case MoneyWithdrawn(amount=amount):
                self.balance -= amount


# ── Repository ─────────────────────────────────────────────────────


class BankAccountRepository(EventSourcedRepository[BankAccount]):
    pass


# ── Commands & Handlers ────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class OpenAccountResult(Response):
    account_id: str


@dataclass(frozen=True, kw_only=True)
class OpenAccountCommand(Request[OpenAccountResult]):
    account_id: str
    owner: str


class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, OpenAccountResult, BankAccount]):
    @override
    def _aggregate_id(self, request: OpenAccountCommand) -> str:
        return request.account_id

    @override
    def _is_creation_command(self, request: OpenAccountCommand) -> bool:
        return True

    @override
    async def _execute(self, request: OpenAccountCommand, aggregate: BankAccount) -> None:
        aggregate.open(request.account_id, request.owner)

    @override
    def _to_response(self, aggregate: BankAccount) -> OpenAccountResult:
        return OpenAccountResult(account_id=aggregate.owner)


@dataclass(frozen=True, kw_only=True)
class DepositResult(Response):
    balance: int


@dataclass(frozen=True, kw_only=True)
class DepositCommand(Request[DepositResult]):
    account_id: str
    amount: int


class DepositHandler(EventSourcedCommandHandler[DepositCommand, DepositResult, BankAccount]):
    @override
    def _aggregate_id(self, request: DepositCommand) -> str:
        return request.account_id

    @override
    async def _execute(self, request: DepositCommand, aggregate: BankAccount) -> None:
        aggregate.deposit(request.account_id, request.amount)

    @override
    def _to_response(self, aggregate: BankAccount) -> DepositResult:
        return DepositResult(balance=aggregate.balance)


# ── Event Handlers (read-side / reactions) ─────────────────────────


class AccountOpenedHandler(EventHandler[AccountOpened]):
    @override
    async def handle(self, event: AccountOpened, /) -> None:
        logger.info('[READ] Account %s opened for %s', event.account_id, event.owner)


class MoneyDepositedHandler(EventHandler[MoneyDeposited]):
    @override
    async def handle(self, event: MoneyDeposited, /) -> None:
        logger.info('[READ] Deposited %d to %s', event.amount, event.account_id)


# ── Module Wiring ──────────────────────────────────────────────────


@module(
    extensions=[
        EventSourcingExtension().bind_aggregate(
            repository=BankAccountRepository,
            event_types=[AccountOpened, MoneyDeposited, MoneyWithdrawn],
        ),
        MediatorExtension()
        .bind_request(OpenAccountCommand, OpenAccountHandler)
        .bind_request(DepositCommand, DepositHandler)
        .bind_event(AccountOpened, [AccountOpenedHandler])
        .bind_event(MoneyDeposited, [MoneyDepositedHandler]),
    ],
)
class BankModule:
    pass


@module(
    imports=[
        BankModule,
        EventSourcingModule.register(EventSourcingConfig()),  # in-memory store, defaults
        MediatorModule.register(),
    ],
)
class AppModule:
    pass


# ── Main ───────────────────────────────────────────────────────────


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)

        # Open account
        result = await mediator.send(OpenAccountCommand(account_id='acc-1', owner='dex'))
        logger.info('Account opened: %s', result)

        # Deposit money
        result2 = await mediator.send(DepositCommand(account_id='acc-1', amount=500))
        logger.info('Balance after deposit: %d', result2.balance)  # ty: ignore[unresolved-attribute]

        # Deposit more
        result3 = await mediator.send(DepositCommand(account_id='acc-1', amount=300))
        logger.info('Balance after second deposit: %d', result3.balance)  # ty: ignore[unresolved-attribute]


if __name__ == '__main__':
    asyncio.run(main())
