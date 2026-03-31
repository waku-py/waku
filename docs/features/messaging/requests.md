---
title: Requests
description: Commands, queries, request handlers, and dispatching via the message bus.
tags:
  - messaging
  - message-bus
  - guide
---

# Requests

A **request** represents an intent that exactly one handler must process. Commands change state
(`PlaceOrder`, `CancelSubscription`), queries read state (`GetUserProfile`, `ListOrders`) —
both are dispatched through the message bus to a single handler that returns a typed response.

Use requests when you need a **guaranteed 1:1 relationship** between a message and its handler.
A `PlaceOrder` command goes to one handler that validates inventory, charges payment, and returns
an order confirmation. If no handler is registered — or if two handlers compete for the same
request type — waku catches the problem at startup.

---

## Defining Requests

waku provides two ways to define a request:

`IRequest[TResponse]` is a marker protocol with no required attributes. Implement it as a
frozen dataclass:

```python linenums="1"
from dataclasses import dataclass

from waku.messaging import IRequest


@dataclass(frozen=True, kw_only=True)
class UserDTO:
    user_id: str
    name: str


@dataclass(frozen=True, kw_only=True)
class GetUserQuery(IRequest[UserDTO]):
    user_id: str


@dataclass(frozen=True, kw_only=True)
class CreateUserCommand(IRequest):  # void command, returns None by default
    name: str
    email: str
```

!!! tip
    `IRequest` without a type argument defaults to `IRequest[None]` — use it for void commands.

Response types are plain frozen dataclasses — no base class is needed:

```python linenums="1"
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class OrderConfirmation:
    order_id: str
    status: str
```

---

## Request Handlers

Each request type maps to **exactly one** handler. Subclass `RequestHandler[TRequest, TResponse]`
and implement the `handle` method:

!!! warning "One handler per request type"
    Registering two handlers for the same `IRequest` type raises `MultipleHandlersRegistered`
    at startup. This is a safety check — ambiguous dispatch is never silent. If multiple
    components need to react to the same trigger, use [events](events.md) instead.

```python linenums="1"
from typing_extensions import override

from waku.messaging import RequestHandler


class GetUserQueryHandler(RequestHandler[GetUserQuery, UserDTO]):
    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    @override
    async def handle(self, request: GetUserQuery, /) -> UserDTO:
        user = await self._user_repo.get(request.user_id)
        return UserDTO(user_id=user.id, name=user.name)
```

---

## Registration

Bind a request to its handler via `MessagingExtension` in the module's `extensions` list:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension


@module(
    extensions=[
        MessagingExtension()
            .bind(GetUserQuery, GetUserQueryHandler)
            .bind(CreateUserCommand, CreateUserCommandHandler),
    ],
)
class UsersModule:
    pass
```

---

## Dispatching

Inject `ISender` and dispatch requests. Prefer `ISender` over `IMessageBus` when you only need to
dispatch requests — this enforces the principle of least privilege.

### `invoke()` — request/response

Returns the response type declared by the request's generic parameter:

```python linenums="1"
from waku.messaging import ISender


async def get_user(sender: ISender, user_id: str) -> UserDTO:
    return await sender.invoke(GetUserQuery(user_id=user_id))
```

If the request declares `IRequest[None]`, `invoke()` returns `None`.

### `send()` — fire-and-forget

Dispatches a message through an [endpoint](routing.md) for background processing.
Unlike `invoke()`, the message is handled asynchronously in a separate DI scope:

!!! info "invoke() vs send()"
    `invoke()` runs the handler inline and returns the response — use it when you need the
    result. `send()` enqueues the message for background processing and returns immediately —
    use it for side-effect-only commands where the caller doesn't wait for a result. See the
    [dispatch method guide](index.md#choosing-a-dispatch-method) for more.

```python linenums="1"
async def create_user(sender: ISender) -> None:
    await sender.send(CreateUserCommand(name='Alice', email='alice@example.com'))
```

!!! tip "How are handler dependencies resolved?"
    Constructor parameters like `user_repo: UserRepository` are resolved automatically by
    waku's [dependency injection](../../fundamentals/providers.md) system. Register the
    implementation in your module's `providers` list.

## Further reading

- **[Events](events.md)** — event definitions, handlers, and publishers
- **[Pipeline Behaviors](pipeline.md)** — cross-cutting middleware for request handling
- **[Routing & Endpoints](routing.md)** — route `send()` to local queues or external systems
- **[Message Bus](index.md)** — setup, interfaces, and complete example
