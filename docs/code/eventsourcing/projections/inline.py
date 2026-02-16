from collections.abc import Sequence

from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.projection.interfaces import IProjection

from app.events import MoneyDeposited, MoneyWithdrawn

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import StreamId


class AccountBalanceProjection(IProjection):
    projection_name = 'account_balance'

    def __init__(self) -> None:
        self.balances: dict[StreamId, int] = {}

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            match event.data:
                case MoneyDeposited(amount=amount):
                    self.balances[event.stream_id] = self.balances.get(event.stream_id, 0) + amount
                case MoneyWithdrawn(amount=amount):
                    self.balances[event.stream_id] = self.balances.get(event.stream_id, 0) - amount
