from waku import module
from waku.messaging import MessagingExtension, MessagingModule
from waku.eventsourcing import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from app.decider import BankAccountDecider
from app.events import AccountOpened, MoneyDeposited
from app.handler import OpenAccountDeciderHandler, OpenAccountRequest
from app.repository import BankAccountDeciderRepository


@module(
    extensions=[
        EventSourcingExtension().bind_decider(
            repository=BankAccountDeciderRepository,
            decider=BankAccountDecider,
            event_types=[AccountOpened, MoneyDeposited],
        ),
        MessagingExtension().bind_request(OpenAccountRequest, OpenAccountDeciderHandler),
    ],
)
class BankDeciderModule:
    pass


@module(
    imports=[
        BankDeciderModule,
        EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore)),
        MessagingModule.register(),
    ],
)
class AppModule:
    pass
