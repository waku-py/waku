from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs import Request, Response
from waku.eventsourcing import EventSourcedCommandHandler

from app.aggregate import BankAccount


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
