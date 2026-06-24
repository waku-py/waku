---
title: Dedicated Consumer
description: Run a separate consumer-only Waku process that consumes from a broker, with graceful SIGTERM shutdown and competing-consumer scale-out.
tags:
  - messaging
  - message-bus
  - consumer
  - deployment
  - guide
---

# Dedicated Consumer

A dedicated consumer is a separate process — its own pod or OS process — that runs a
`WakuApplication` configured only to consume from a broker, apart from your API or web process.
It is not a special mode or a `consumer=True` switch: it is the same application, assembled from
consumer-shaped config and launched with `app.run()`.

A pure consumer declares `transports` + `inbound` + `inbox` and omits `outbox` and HTTP routes. A
consumer whose handlers also *produce* external messages adds `outbox` and an external endpoint —
listeners and senders are independent. This mirrors Wolverine, where a
[dedicated worker](https://wolverine.netlify.app/guide/runtime.html) is just another full node
that happens to listen to a queue.

Because Waku owns the embedded FastStream broker through the transport lifecycle, the consumer
keeps full Waku resilience — the same inbound listener path, requeue, and circuit-breaker behavior
as any in-process listener. FastStream is the broker mechanism; the consume loop is Waku's.

## Minimal setup

```python linenums="1"
import asyncio
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.messaging import (
    EventHandler,
    IEvent,
    IInboxStore,
    InboundConfig,
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    listen,
)
from waku.messaging.transport.faststream.rabbitmq import rabbit_transport


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str


class OrderPlacedHandler(EventHandler[OrderPlaced]):
    @override
    async def handle(self, message: OrderPlaced, /) -> None:
        print(f'handling order {message.order_id}')


def build_config() -> MessagingConfig:
    return MessagingConfig(
        transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
        inbound=InboundConfig(listeners=[listen('rabbitmq://orders')]),
        inbox=InboxConfig(store=build_inbox_store),  # your IInboxStore, e.g. the SQLAlchemy adapter
    )


@module(
    imports=[MessagingModule.register(build_config())],
    extensions=[MessagingExtension().bind(OrderPlacedHandler)],
)
class ConsumerModule:
    pass


async def main() -> None:
    app = WakuFactory(ConsumerModule).create()
    await app.run()  # blocks until SIGTERM/SIGINT, then drains + closes the broker


if __name__ == '__main__':
    asyncio.run(main())
```

Handlers bind by message *type* (`MessagingExtension().bind(...)`), not by the queue they arrive
on — the same routing as the rest of the message bus. The full runnable file lives at
`examples/messaging/consumer.py`.

## What `run()` does

`app.run()` enters the application context and blocks until a shutdown signal:

1. `async with app` runs the after-init extensions: it starts the embedded broker, subscribes the
   inbound listener to its queue, starts the durable inbox receiver's dispatch loop, and arms
   per-pod crash recovery.
2. It then parks until `SIGTERM` or `SIGINT` arrives. Kubernetes sends `SIGTERM` when terminating
   a pod.
3. On the signal it exits the context, which drains in-flight work and closes the broker.

`app.request_shutdown()` releases `run()` programmatically — for embedding it in a larger runtime,
or for tests that cannot deliver a real signal.

Plain `asyncio.run(main())` does not trap `SIGTERM`, so without `run()` a pod termination would
kill messages mid-processing. `run()` converts the signal into a clean context exit so
`on_app_shutdown` drains instead of the process being abruptly killed.

## Crash safety

Graceful drain is a nicety, not a correctness gate. The inbox persists each message *before* it is
acked to the broker, so an abrupt `SIGKILL` (or a node failure) loses no work: the un-acked message
is redelivered, and any message already persisted is re-dispatched by crash recovery on restart.
Delivery is at-least-once, and the inbox `(id, handler)` dedup absorbs the duplicate.

## Scale-out

Run N identical consumer pods. RabbitMQ treats them as competing consumers and distributes messages
across them; the inbox `(id, handler)` dedup absorbs any double delivery; and each pod runs its own
crash recovery, claiming rows with `FOR UPDATE SKIP LOCKED` so two pods never recover the same
message. There is **no leader election** — recovery is concurrency-safe by construction, matching
the 1–3-pod deployment topology. This is Wolverine's model: plain queue listeners are
competing-consumer by default, and leader election governs only sticky agents, not parallel
listeners.

!!! warning "Per-group ordering is not preserved across pods"
    A listener with `partition_by` (per-group FIFO) keeps order only within a single consumer.
    Plain RabbitMQ competing consumers spread a group's messages across pods, so per-group order is
    not preserved at scale. Enforcing it needs a single-active-consumer queue or a consistent-hash
    exchange — out of scope for the dedicated-consumer deployment itself.
