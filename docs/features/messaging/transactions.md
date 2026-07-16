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
the implementation comes from your durability backend (or your own provider in a backendless
app). Any class satisfying the protocol can serve as the unit of work for `TransactionalBehavior`.

---

## SQLAlchemy Adapter

!!! info "Requires `waku[sqla]`"
    Install the SQLAlchemy extra: `uv add waku --extra sqla`

`SqlAlchemyUnitOfWork` wraps an `AsyncSession` and delegates `commit()` / `rollback()` to it.
The [SQLAlchemy backend](../../fundamentals/backends.md) registers it automatically over the same
scoped session every durable store uses:

```python linenums="1"
from waku.backends.sqlalchemy import SqlAlchemyBackend

# imports=[..., SqlAlchemyBackend.register(session_factory=create_session)]
```

---

## TransactionalBehavior

`TransactionalBehavior` follows a strict owner sequence:

1. Call `call_next()` (the handler, plus any remaining behaviors).
2. Nested inline invocations join the same physical transaction; only the outermost behavior may finish it.
3. On normal completion: `uow.commit()` before returning success.
4. On a handler failure or cancellation: complete a shielded `uow.rollback()`, then preserve the failure or cancellation.
5. On a commit failure or commit cancellation: complete a shielded rollback, then preserve the commit failure or cancellation.

Success therefore means the commit completed, not merely that the handler returned. Likewise, a retry, absorbed failure,
or fallback result is allowed only after rollback completed. If that cleanup fails, the cleanup error escapes instead of
the framework reporting a normal retry/fallback/failure result. When a handler failure or cancellation is already being
preserved and that shielded rollback itself fails, the cleanup failure is never swallowed: a cancellation stays
authoritative and carries the rollback failure as its cause, while a preserved ordinary failure gives way to the escaping
cleanup error, which retains the original failure as context.

### Nested rollback-only failure

If a nested inline handler fails, the shared transaction becomes rollback-only. Catching that nested exception does not
make the transaction committable again: when the outer handler returns, waku rolls the transaction back and raises
`UnexpectedRollbackError` from the root package. The original nested failure is retained as its cause.

```python linenums="1"
from waku import UnexpectedRollbackError
```

Cancellation is never converted into `UnexpectedRollbackError` or a normal failure result. It remains cancellation after
shielded rollback. Deferred non-durable cascading messages also run only after committed success, so neither a direct
failure nor a swallowed nested failure can flush them after rollback.

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

A complete setup with the SQLAlchemy backend and `TransactionalBehavior`:

```python linenums="1"
from waku import module
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.messaging import MessagingConfig, MessagingModule
from waku.messaging.behaviors.transactional import TransactionalBehavior


@module(
    imports=[
        MessagingModule.register(
            MessagingConfig(
                global_pipeline_behaviors=[TransactionalBehavior],
            ),
        ),
        SqlAlchemyBackend.register(session_factory=create_session),
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
- **[Outbox](outbox.md)** — outbox uses UoW for transactional persistence
- **[Message Bus](index.md)** — setup, interfaces, and complete example
