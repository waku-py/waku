---
title: Transactions
description: Wrap message handling in database transactions using TransactionalBehavior and IUnitOfWork.
tags:
  - messaging
  - message-bus
  - transactions
  - guide
---

# Transactions

When a handler modifies the database, you need guarantees: if the handler fails, changes roll
back. `TransactionalBehavior` is a [pipeline behavior](pipeline.md) that wraps message handling
in a unit-of-work commit/rollback cycle — commit on success, rollback on any failure, including
failures during the commit itself. This is especially important when handlers write to the
[transactional outbox](outbox.md), where the outbox write must be atomic with the business data.

---

## IUnitOfWork Protocol

`IUnitOfWork` is a two-method protocol that lives at the top level (`waku.uow`), not inside
messaging — it's a general infrastructure concern usable by any layer:

```python linenums="1"
from waku.uow import IUnitOfWork
```

The protocol defines two methods — `commit()` and `rollback()`. waku provides the interface;
you provide an implementation. Any class satisfying the protocol can serve as the unit of work
for `TransactionalBehavior`.

---

## SQLAlchemy Adapter

!!! info "Requires `waku[sqla]`"
    Install the SQLAlchemy extra: `uv add waku --extra sqla`

`SqlAlchemyUnitOfWork` wraps an `AsyncSession` and delegates `commit()` / `rollback()` to it:

```python linenums="1"
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
```

Register it in your infrastructure module, mapping the implementation to `IUnitOfWork`:

```python linenums="1"
from waku import module
from waku.di import scoped
from waku.uow import IUnitOfWork
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork


@module(
    providers=[
        scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
    ],
)
class InfraModule: ...
```

`SqlAlchemyUnitOfWork` receives the `AsyncSession` via dependency injection, so make sure you
have a session provider registered in one of your modules.

---

## TransactionalBehavior

`TransactionalBehavior` follows a strict commit/rollback sequence:

1. Call `call_next()` (the handler, plus any remaining behaviors).
2. On success: `uow.commit()`.
3. On handler exception: `uow.rollback()`, re-raise.
4. On commit exception: `uow.rollback()`, re-raise.

Register it as a global pipeline behavior:

```python linenums="1"
from waku.messaging import MessagingConfig, MessagingModule
from waku.messaging.behaviors.transactional import TransactionalBehavior

MessagingModule.register(
    MessagingConfig(
        global_pipeline_behaviors=[TransactionalBehavior],
    ),
)
```

!!! warning
    When registered globally, `TransactionalBehavior` applies to **every** message flowing
    through the bus — including read-only queries. This means every query opens and commits
    a transaction, even when there are no writes.

!!! tip
    Use [per-request behaviors](pipeline.md#per-request-behaviors) if you only want
    transactions on write commands:

    ```python linenums="1"
    from waku import module
    from waku.messaging import MessagingExtension
    from waku.messaging.behaviors.transactional import TransactionalBehavior


    @module(
        extensions=[
            MessagingExtension().bind(
                CreateOrderCommand,
                CreateOrderCommandHandler,
                behaviors=[TransactionalBehavior],
            ),
        ],
    )
    class OrderModule: ...
    ```

---

## Wiring Example

A complete setup with SQLAlchemy session, unit of work, and `TransactionalBehavior`:

```python linenums="1"
from waku import module
from waku.di import scoped
from waku.uow import IUnitOfWork
from waku.messaging import MessagingConfig, MessagingModule
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
from waku.messaging.behaviors.transactional import TransactionalBehavior


@module(
    providers=[
        scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
    ],
)
class InfraModule: ...


@module(
    imports=[
        InfraModule,
        MessagingModule.register(
            MessagingConfig(
                global_pipeline_behaviors=[TransactionalBehavior],
            ),
        ),
    ],
)
class AppModule: ...
```

---

## Custom UoW

Implement `IUnitOfWork` for any backend — the protocol requires only `commit` and `rollback`:

```python linenums="1"
from waku.uow import IUnitOfWork


class MongoUnitOfWork(IUnitOfWork):
    def __init__(self, session: AsyncIOMotorClientSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit_transaction()

    async def rollback(self) -> None:
        await self._session.abort_transaction()
```

Register it the same way as the SQLAlchemy adapter:

```python linenums="1"
scoped(IUnitOfWork, MongoUnitOfWork)
```

---

## Further reading

- **[Pipeline Behaviors](pipeline.md)** — defining, registering, and ordering behaviors
- **[Routing & Endpoints](routing.md)** — route messages to background endpoints
- **[Outbox & Transport](outbox.md)** — outbox uses UoW for transactional persistence
- **[Message Bus](index.md)** — setup, interfaces, and complete example
