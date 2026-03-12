from dataclasses import dataclass

from typing_extensions import override

from waku.messaging import IRequest
from waku.eventsourcing import EventSourcedCommandHandler

from app.aggregate import BankAccount


@dataclass(frozen=True, kw_only=True)
class OpenAccountResult:
    account_id: str


@dataclass(frozen=True, kw_only=True)
class OpenAccountCommand(IRequest[OpenAccountResult]):
    account_id: str
    owner: str


class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, OpenAccountResult, BankAccount]):
    @override
    def _is_creation_command(self, request: OpenAccountCommand) -> bool:
        return True

    @override
    def _aggregate_id(self, request: OpenAccountCommand) -> str:
        return request.account_id

    @override
    async def _execute(self, request: OpenAccountCommand, aggregate: BankAccount) -> None:
        aggregate.open(request.account_id, request.owner)

    @override
    def _to_response(self, aggregate: BankAccount) -> OpenAccountResult:
        return OpenAccountResult(account_id=aggregate.account_id)


@dataclass(frozen=True, kw_only=True)
class DepositResult:
    balance: int


@dataclass(frozen=True, kw_only=True)
class DepositCommand(IRequest[DepositResult]):
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
