from waku import module
from waku.eventsourcing import EventSourcingExtension
from waku.eventsourcing.snapshot.strategy import EventCountStrategy

from app.events import AccountOpened, MoneyDeposited, MoneyWithdrawn
from app.repository import BankAccountSnapshotRepository


@module(
    extensions=[
        EventSourcingExtension().bind_aggregate(
            repository=BankAccountSnapshotRepository,
            event_types=[AccountOpened, MoneyDeposited, MoneyWithdrawn],
            snapshot_strategy=EventCountStrategy(threshold=50),
        ),
    ],
)
class BankSnapshotModule:
    pass
