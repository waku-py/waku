from waku import module
from waku.cqrs import MediatorExtension, MediatorModule
from waku.eventsourcing import EventSourcingConfig, EventSourcingExtension, EventSourcingModule

from app.commands import (
    DepositCommand,
    DepositHandler,
    OpenAccountCommand,
    OpenAccountHandler,
)
from app.events import AccountOpened, MoneyDeposited, MoneyWithdrawn
from app.repository import BankAccountRepository


@module(
    extensions=[
        EventSourcingExtension().bind_aggregate(
            repository=BankAccountRepository,
            event_types=[AccountOpened, MoneyDeposited, MoneyWithdrawn],
        ),
        MediatorExtension()
        .bind_request(OpenAccountCommand, OpenAccountHandler)
        .bind_request(DepositCommand, DepositHandler),
    ],
)
class BankModule:
    pass


@module(
    imports=[
        BankModule,
        EventSourcingModule.register(EventSourcingConfig()),
        MediatorModule.register(),
    ],
)
class AppModule:
    pass
