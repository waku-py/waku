from waku import module
from waku.eventsourcing import EventSourcingExtension
from waku.eventsourcing.snapshot.strategy import EventCountStrategy

from app.decider import BankAccountDecider
from app.events import AccountOpened, MoneyDeposited
from app.repository import BankAccountSnapshotRepository


@module(
    extensions=[
        EventSourcingExtension().bind_decider(
            repository=BankAccountSnapshotRepository,
            decider=BankAccountDecider,
            event_types=[AccountOpened, MoneyDeposited],
            snapshot_strategy=EventCountStrategy(threshold=50),
        ),
    ],
)
class BankSnapshotModule:
    pass
