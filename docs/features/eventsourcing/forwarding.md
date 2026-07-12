---
title: Event Forwarding
description: Forward event-store appends into the message bus in the same command flow, without a hand-written dispatcher.
tags:
  - event-sourcing
  - messaging
  - integration
  - guide
---

# Event Forwarding

Forwarding propagates event-store appends into the message bus as part of the same command flow. When
an aggregate appends an event, the framework hands it to the bus — so an event-sourced write can
trigger integration events (or inline reactions) without a hand-written dispatcher. This is the
Python analog of [Marten's event forwarding](https://martendb.io/events/subscriptions.html).

## Enabling forwarding

Forwarding is the seam between the event-sourcing and messaging modules, so it needs three modules
wired together:

```python linenums="1"
from waku import module
from waku.eventsourcing import EventSourcingConfig, EventSourcingModule, forward
from waku.integrations.eventsourcing_messaging import EventSourcingMessagingModule
from waku.messaging import MessagingModule


@module(
    imports=[
        EventSourcingModule.register(  # (1)!
            EventSourcingConfig(
                forwarding=[forward(AccountOpened).transformed_to(to_integration_event)],
            ),
        ),
        MessagingModule.register(),           # (2)!
        EventSourcingMessagingModule.register(),  # (3)!
        SqlAlchemyBackend.register(session_factory=create_session),  # (4)!
    ],
)
class AppModule:
    pass
```

1. Event-sourcing config, including the `forwarding` rules.
2. The message bus the events are forwarded onto.
3. The bridge that actually wires forwarding — without it, the rules do nothing.
4. The [durability backend](../../fundamentals/backends.md) providing the recording event store.

!!! warning "The bridge is required — misconfiguration is fail-loud"
    `forwarding=[...]` only takes effect through `EventSourcingMessagingModule.register()`. Configure
    forwarding without the bridge and startup raises `ImproperlyConfiguredError`: *forwarding=[...] is
    configured but no forwarding consumer is installed, so appended events are silently dropped. Import
    EventSourcingMessagingModule.register() to wire the ES<->messaging bridge that forwards appended
    events to the message bus.*

The bridge also auto-registers a `CorrelationEnricher`, so events appended inside an active message
context carry its correlation/causation ids in their stored metadata. Pass
`EventSourcingMessagingModule.register(enrich_correlation=False)` to opt out.

## What gets forwarded

Every appended event is forwarded. By default it is forwarded **raw** via `publish`, post-commit and
**subscriber-gated** — if no handler is registered for the event type, it is silently dropped. A
`forward(...)` rule customizes a specific event type:

- `forward(EventType).transformed_to(fn)` maps the appended event to an integration event before
  forwarding it via `publish` (still post-commit, still subscriber-gated).
- `forward(EventType).same_transaction()` forwards inline via `invoke`, in the command's own
  transaction — fail-fast, so it raises `HandlerNotFound` if no handler is registered.

The transform receives the appended `StoredEvent`: read the domain payload via `stored.data`, and the
stream provenance via `stored.stream_id`, `stored.position`, `stored.global_position`, and
`stored.timestamp`:

```python linenums="1"
from waku.eventsourcing import EventSourcingConfig, forward
from waku.eventsourcing.contracts.event import StoredEvent


def to_integration_event(stored: StoredEvent) -> AccountOpenedIntegration:
    return AccountOpenedIntegration(
        account_id=stored.data.account_id,   # the appended domain event
        stream_id=str(stored.stream_id),     # its stream provenance
    )


config = EventSourcingConfig(
    forwarding=[forward(AccountOpened).transformed_to(to_integration_event)],
)
```

!!! danger "Forwarding requires a recording store"
    Only a store that records appended events feeds forwarding. The SQLAlchemy backend's
    `SqlAlchemyEventStore` records; `InMemoryEventStore` (the memory backend) does not. Configuring forwarding against a non-recording store raises
    `ImproperlyConfiguredError` at startup naming the store type: *forwarding=[...] is configured
    against InMemoryEventStore, which does not record appended events...*. A custom store opts in by
    overriding `IEventWriter.records_appended_events` (default `False`) to `True`.

## Complete example

A three-module program: a `BankAccount` aggregate whose `AccountOpened` event is forwarded as an
`AccountOpenedIntegration` event to a subscriber, backed by a recording PostgreSQL store.

```python linenums="1"
import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from typing_extensions import override

from waku import WakuFactory, module
from waku.di import object_, scoped
from waku.eventsourcing import (
    EventSourcedAggregate,
    EventSourcedRepository,
    EventSourcingConfig,
    EventSourcingExtension,
    EventSourcingModule,
    forward,
)
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.eventsourcing.contracts.event import StoredEvent
from waku.integrations.eventsourcing_messaging import (
    EventSourcedCommandHandler,
    EventSourcingMessagingModule,
)
from waku.messages import IEvent
from waku.messaging import EventHandler, IMessageBus, IRequest, MessagingExtension, MessagingModule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = 'postgresql+psycopg://waku:waku@localhost:15432/waku_es'


@dataclass(frozen=True, kw_only=True)
class AccountOpened(IEvent):
    account_id: str
    owner: str


@dataclass(frozen=True, kw_only=True)
class AccountOpenedIntegration(IEvent):
    account_id: str
    stream_id: str


def to_integration_event(stored: StoredEvent) -> AccountOpenedIntegration:
    return AccountOpenedIntegration(account_id=stored.data.account_id, stream_id=str(stored.stream_id))


class BankAccount(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.account_id: str = ''
        self.owner: str = ''

    def open(self, account_id: str, owner: str) -> None:
        self._raise_event(AccountOpened(account_id=account_id, owner=owner))

    @override
    def _apply(self, event: IEvent) -> None:
        if isinstance(event, AccountOpened):
            self.account_id = event.account_id
            self.owner = event.owner


class BankAccountRepository(EventSourcedRepository[BankAccount]):
    pass


@dataclass(frozen=True, kw_only=True)
class OpenAccountResult:
    account_id: str


@dataclass(frozen=True, kw_only=True)
class OpenAccountCommand(IRequest[OpenAccountResult]):
    account_id: str
    owner: str


class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, BankAccount, OpenAccountResult]):
    @override
    def _is_creation_command(self, request: OpenAccountCommand) -> bool:
        return True

    @override
    def _aggregate_id(self, request: OpenAccountCommand) -> str:
        return request.account_id

    @override
    async def _execute(self, request: OpenAccountCommand, aggregate: BankAccount) -> None:
        aggregate.open(request.account_id, request.owner)

    @override
    def _to_response(self, aggregate: BankAccount) -> OpenAccountResult:
        return OpenAccountResult(account_id=aggregate.account_id)


class AccountOpenedIntegrationHandler(EventHandler[AccountOpenedIntegration]):
    @override
    async def handle(self, event: AccountOpenedIntegration, /) -> None:
        logger.info('[integration] account %s opened on stream %s', event.account_id, event.stream_id)


metadata = MetaData()
engine = create_async_engine(DATABASE_URL)


async def create_session(engine_: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine_, expire_on_commit=False) as session:
        yield session


@module(
    extensions=[
        EventSourcingExtension().bind_aggregate(
            repository=BankAccountRepository,
            event_types=[AccountOpened],
        ),
        MessagingExtension()
        .bind(OpenAccountCommand, OpenAccountHandler)
        .bind(AccountOpenedIntegration, AccountOpenedIntegrationHandler),
    ],
)
class BankModule:
    pass


@module(
    imports=[
        BankModule,
        EventSourcingModule.register(
            EventSourcingConfig(
                forwarding=[forward(AccountOpened).transformed_to(to_integration_event)],
            ),
        ),
        MessagingModule.register(),
        EventSourcingMessagingModule.register(),
        SqlAlchemyBackend.register(session_factory=create_session, metadata=metadata),
    ],
    providers=[
        object_(engine, provided_type=AsyncEngine),
    ],
)
class AppModule:
    pass


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    try:
        app = WakuFactory(AppModule).create()
        async with app, app.container() as container:
            bus = await container.get(IMessageBus)
            await bus.invoke(OpenAccountCommand(account_id='acc-1', owner='dex'))
            # AccountOpenedIntegrationHandler runs asynchronously as the forwarded publish drains.
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
```

Because the default rule forwards via `publish`, the subscriber runs asynchronously after the command
commits — it drains as the local queue endpoint is worked (including on shutdown). Use
`.same_transaction()` instead if the reaction must run inline in the command's transaction.

!!! note "forward XOR cascade"
    An event forwarded here should not also be emitted as a
    [handler cascade](../messaging/events.md#cascading-messages) of the same type — the two paths would
    deliver it twice. Choose one per event type.

## Further reading

- **[Aggregates](aggregates.md)** — defining event-sourced aggregates and command handlers
- **[Events](../messaging/events.md)** — event handlers, publishing, and cascading messages
- **[Outbox](../messaging/outbox.md)** — durable forwarding to external brokers
- **[Transactions](../messaging/transactions.md)** — the unit of work behind `same_transaction` forwarding
