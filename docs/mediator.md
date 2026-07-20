---
title: waku as in-process mediator
description: The smallest way to use waku — invoke requests and publish events in-process, no transports.
tags:
  - messaging
  - mediator
  - getting-started
---

# waku as in-process mediator

The smallest way to use waku is as an **in-process mediator**: dispatch commands and queries to
handlers, and publish events to in-process subscribers, without touching a broker, an outbox, or a
database. If you have used MediatR (.NET) or a similar in-process bus, this is the familiar shape —
decoupled handlers, resolved and invoked through one interface. Everything else in the messaging
docs builds outward from here.

You need only the core install:

```bash
uv add waku
```

---

## Requests in process

Inject `ISender` and `invoke` a request. It runs **inline** — synchronously in your task — and
returns the handler's typed response. Exactly one handler may be bound to a request type.

```python linenums="1"
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.messaging import (
    IRequest,
    ISender,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)


@dataclass(frozen=True, kw_only=True)
class GetGreeting(IRequest[str]):
    name: str


class GetGreetingHandler(RequestHandler[GetGreeting, str]):
    @override
    async def handle(self, request: GetGreeting, /) -> str:
        return f'Hello, {request.name}!'


@module(
    imports=[MessagingModule.register()],
    extensions=[MessagingExtension().bind(GetGreetingHandler)],
)
class AppModule:
    pass


async def main() -> None:
    app = WakuFactory(AppModule).create()
    async with app, app.container() as container:
        sender = await container.get(ISender)
        greeting = await sender.invoke(GetGreeting(name='World'))
        print(greeting)  # Hello, World!
```

No `MessagingConfig` is needed — `MessagingModule.register()` uses the defaults, and `invoke` never
routes through an endpoint.

---

## Events in process

Inject `IPublisher` and `publish` an event. It fans out to **every** handler bound to the event, in
the same process. With no transports configured, publish delivers in-process only; if no handler is
bound, it is a silent no-op.

```python linenums="1"
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging import EventHandler, IPublisher


class UserRegistered(IEvent):
    ...


class SendWelcomeEmail(EventHandler[UserRegistered]):
    @override
    async def handle(self, event: UserRegistered, /) -> None:
        ...  # send the email


async def announce(publisher: IPublisher, event: UserRegistered) -> None:
    await publisher.publish(event)
```

Bind `SendWelcomeEmail` the same way as a request handler
(`MessagingExtension().bind(SendWelcomeEmail)`).

---

## Growing beyond in-process

When you need work to run in the background, survive a restart, or leave the process for a broker,
you add configuration — you do not change how you dispatch:

- Move a handler off the caller's task, or make delivery durable → [Runtime & delivery semantics](features/messaging/runtime.md).
- Route messages to a queue or an external system → [Routing & endpoints](features/messaging/routing.md).
- Send reliably across a process boundary → [Outbox](features/messaging/outbox.md).

---

## Further reading

- **[Requests](features/messaging/requests.md)** — request/response handlers in depth
- **[Events & cascading](features/messaging/events.md)** — event handlers and fan-out
- **[Runtime & delivery semantics](features/messaging/runtime.md)** — what changes when you leave in-process
