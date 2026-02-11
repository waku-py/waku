"""Event Sourcing with PostgreSQL (SqlAlchemy) Store.

Same BankAccount domain as basic_usage.py, wired with SqlAlchemyEventStore
against a real PostgreSQL database.

Prerequisites:
    docker compose -f examples/eventsourcing/postgres/compose.yaml up -d
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator  # noqa: TC003  # Dishka needs runtime access
from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
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
from waku.di import object_, scoped
from waku.eventsourcing import (
    EventSourcedAggregate,
    EventSourcedCommandHandler,
    EventSourcedRepository,
    EventSourcingConfig,
    EventSourcingExtension,
    EventSourcingModule,
)
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = 'postgresql+psycopg://waku:waku@localhost:15432/waku_es'


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


# ── PostgreSQL Wiring ──────────────────────────────────────────────

metadata = MetaData()
tables = bind_event_store_tables(metadata)
engine = create_async_engine(DATABASE_URL, echo=False)


async def create_session(engine_: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine_, expire_on_commit=False) as session:
        yield session


es_config = EventSourcingConfig(
    store_factory=make_sqlalchemy_event_store(tables),
    event_serializer=JsonEventSerializer,
)


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
        EventSourcingModule.register(es_config),
        MediatorModule.register(),
    ],
    providers=[
        object_(engine, provided_type=AsyncEngine),
        scoped(AsyncSession, create_session),
    ],
)
class AppModule:
    pass


# ── Main ───────────────────────────────────────────────────────────


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    try:
        app = WakuFactory(AppModule).create()

        async with app, app.container() as container:
            mediator = await container.get(IMediator)

            result = await mediator.send(OpenAccountCommand(account_id='acc-1', owner='dex'))
            logger.info('Account opened: %s', result)

            result2 = await mediator.send(DepositCommand(account_id='acc-1', amount=500))
            logger.info('Balance after deposit: %d', result2.balance)  # ty: ignore[unresolved-attribute]

            result3 = await mediator.send(DepositCommand(account_id='acc-1', amount=300))
            logger.info('Balance after second deposit: %d', result3.balance)  # ty: ignore[unresolved-attribute]
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
