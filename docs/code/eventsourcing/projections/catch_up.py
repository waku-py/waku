from collections.abc import Sequence
from dataclasses import dataclass

from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection

from app.events import AccountOpened, MoneyDeposited, MoneyWithdrawn


@dataclass
class AccountSummary:
    owner: str = ''
    balance: int = 0
    transaction_count: int = 0


class AccountSummaryProjection(ICatchUpProjection):
    projection_name = 'account_summary'
    error_policy = ErrorPolicy.RETRY

    def __init__(self) -> None:
        self.summaries: dict[str, AccountSummary] = {}

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            stream_key = event.stream_id.stream_key
            summary = self.summaries.setdefault(stream_key, AccountSummary())

            match event.data:
                case AccountOpened(owner=owner):
                    summary.owner = owner
                case MoneyDeposited(amount=amount):
                    summary.balance += amount
                    summary.transaction_count += 1
                case MoneyWithdrawn(amount=amount):
                    summary.balance -= amount
                    summary.transaction_count += 1

    async def teardown(self) -> None:
        self.summaries.clear()
