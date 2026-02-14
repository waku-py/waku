from waku import module
from waku.cqrs import MediatorExtension, MediatorModule
from waku.eventsourcing import EventSourcingConfig, EventSourcingExtension, EventSourcingModule

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
        MediatorExtension().bind_request(OpenAccountRequest, OpenAccountDeciderHandler),
    ],
)
class BankDeciderModule:
    pass


@module(
    imports=[
        BankDeciderModule,
        EventSourcingModule.register(EventSourcingConfig()),
        MediatorModule.register(),
    ],
)
class AppModule:
    pass
