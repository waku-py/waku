---
title: Requests
description: Commands, queries, request handlers, and dispatching via the message bus.
tags:
  - messaging
  - message-bus
  - guide
---

# Requests

A **request** is a frozen dataclass that describes an intent. Commands change state, queries read
state — both are dispatched through the message bus to exactly one handler.

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

Response types are plain frozen dataclasses -- no base class is needed:

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
            .bind_request(GetUserQuery, GetUserQueryHandler)
            .bind_request(CreateUserCommand, CreateUserCommandHandler),
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

Dispatches a request through the same handler and pipeline, but discards the return value.
Use it for side-effect-only commands where the caller does not need a result:

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
- **[Message Bus](index.md)** — setup, interfaces, and complete example
