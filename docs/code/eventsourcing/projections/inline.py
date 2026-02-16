from collections.abc import Sequence

from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.projection.interfaces import IProjection

from app.events import MoneyDeposited, MoneyWithdrawn


class AccountBalanceProjection(IProjection):
    projection_name = 'account_balance'

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            stream_key = event.stream_id.split('-', 1)[1]
            match event.data:
                case MoneyDeposited(amount=amount):
                    self.balances[stream_key] = self.balances.get(stream_key, 0) + amount
                case MoneyWithdrawn(amount=amount):
                    self.balances[stream_key] = self.balances.get(stream_key, 0) - amount
