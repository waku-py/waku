from waku import module
from waku.messaging import MessagingExtension, MessagingModule
from waku.backends.memory import MemoryBackend
from waku.eventsourcing import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.integrations.eventsourcing_messaging import EventSourcingMessagingModule

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
        MessagingExtension().bind(OpenAccountCommand, OpenAccountHandler).bind(DepositCommand, DepositHandler),
    ],
)
class BankModule:
    pass


@module(
    imports=[
        BankModule,
        EventSourcingModule.register(EventSourcingConfig()),
        MemoryBackend.register(),
        EventSourcingMessagingModule.register(),
        MessagingModule.register(),
    ],
)
class AppModule:
    pass
