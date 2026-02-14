from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs import INotification, Request, Response
from waku.eventsourcing import DeciderCommandHandler

from app.decider import BankCommand, OpenAccount
from app.state import BankAccountState


@dataclass(frozen=True, kw_only=True)
class OpenAccountResult(Response):
    owner: str


@dataclass(frozen=True, kw_only=True)
class OpenAccountRequest(Request[OpenAccountResult]):
    account_id: str
    owner: str


class OpenAccountDeciderHandler(
    DeciderCommandHandler[
        OpenAccountRequest,
        OpenAccountResult,
        BankAccountState,
        BankCommand,
        INotification,
    ],
):
    @override
    def _aggregate_id(self, request: OpenAccountRequest) -> str:
        return request.account_id

    @override
    def _to_command(self, request: OpenAccountRequest) -> BankCommand:
        return OpenAccount(account_id=request.account_id, owner=request.owner)

    @override
    def _is_creation_command(self, request: OpenAccountRequest) -> bool:
        return True

    @override
    def _to_response(self, state: BankAccountState, version: int) -> OpenAccountResult:
        return OpenAccountResult(owner=state.owner)
