from collections.abc import Sequence

from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.projection.interfaces import IProjection


class AccountBalanceProjection(IProjection):
    projection_name = 'account_balance'

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            match event.event_type:
                case 'MoneyDeposited':
                    stream_key = event.stream_id.split('-', 1)[1]
                    self.balances[stream_key] = self.balances.get(stream_key, 0) + event.data.amount
                case 'MoneyWithdrawn':
                    stream_key = event.stream_id.split('-', 1)[1]
                    self.balances[stream_key] = self.balances.get(stream_key, 0) - event.data.amount
