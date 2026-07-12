---
title: 'Transport: RabbitMQ'
description: The RabbitMQ transport — publishing via the outbox relay, listening, disposition mapping, and the default wire mapper.
tags:
  - messaging
  - transport
  - rabbitmq
  - guide
---

# Transport: RabbitMQ

The RabbitMQ transport is a FastStream-backed wire adapter. You register it on
`MessagingConfig.transports` keyed by the `rabbitmq` scheme; both the outbound
[outbox relay](../outbox.md) and inbound [listeners](../dedicated-consumer.md) then use it. The
factory `rabbit_transport(url, *, prefetch_count=250, mapper=None)` returns a deferred
`TransportFactory` that the framework invokes once at startup to build the transport, which opens two
connections — one for publishing, one for consuming.

```python linenums="1"
from waku.messaging import MessagingConfig
from waku.messaging.transport.faststream import rabbit_transport

config = MessagingConfig(
    transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
)
```

---

## Publishing

Messages routed to a `rabbitmq://…` [external endpoint](../routing.md) are persisted to the outbox
and dispatched by the relay through this transport — you never publish to the broker directly. The
relay hands the transport an already-encoded body plus the envelope metadata; the transport puts it
on the wire without re-serializing. See [Outbox](../outbox.md) for the endpoint and routing wiring.

Every publish carries AMQP `DeliveryMode.PERSISTENT`, so a message in a durable queue survives a
broker restart — required for the outbox's at-least-once guarantee, since the relay retires the
outbox row as soon as the broker accepts the message. A custom
[envelope mapper](../envelope-mapper.md) can opt out per message with `RabbitOutgoing(persist=False)`.

---

## Listening

`listen('rabbitmq://<queue>')` subscribes an inbound listener to a queue. Each delivered message is
persisted to the [inbox](../inbox.md) before its handler runs, then acknowledged. `prefetch_count`
(default 250) bounds how many unacknowledged messages the broker delivers at once, which is the first
lever for inbound flow control alongside [backpressure and the circuit breaker](../resilience.md).

```python linenums="1"
from waku.messaging import MessagingConfig, listen
from waku.messaging.transport.faststream import rabbit_transport

config = MessagingConfig(
    endpoints=[listen('rabbitmq://orders')],
    transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/', prefetch_count=100)},
)
```

See [Dedicated consumer node](../dedicated-consumer.md) for the full consumer wiring (inbox store and
unit of work).

---

## Disposition map

The listener uses manual acknowledgement and translates each processing outcome into one broker
disposition:

| Processing outcome | Broker call | Effect |
|---|---|---|
| handled successfully | `ack()` | the message is removed from the queue |
| requeue | `nack(requeue=True)` | RabbitMQ redelivers the message |
| reject (poison) | `reject()` | no requeue — routed to the dead-letter exchange, or dropped |

`nack(requeue=True)` is a RabbitMQ-specific redelivery; other brokers map these outcomes
differently.

---

## Dead-letter

`reject()` hands the message to RabbitMQ's own dead-letter exchange when the queue has one
configured. This is the **broker's** DLX and is independent of waku's dead-letter store: waku's
retry/dead-letter escalation runs at the processing layer, before a message is ever rejected. See
[Error handling](../error-handling.md) for waku's dead-letter model.

---

## Mapper default

Out of the box the transport uses `DefaultRabbitEnvelopeMapper`, which writes the Wolverine two-tier
header layout (framework fields as bare broker headers, payload as the body). Override the wire
format — for interop with a foreign producer or to reach AMQP-native fields like `priority` — by
passing `mapper=` to `rabbit_transport`, or per listener via `listen(..., mapper=…)`. See
[Envelope mapper](../envelope-mapper.md).

---

## Further reading

- **[Outbox](../outbox.md)** — the send side: external endpoints, relay, and persistence
- **[Transport: Kafka](kafka.md)** — the same model for Kafka
- **[Envelope mapper](../envelope-mapper.md)** — customize the wire format
- **[Dedicated consumer node](../dedicated-consumer.md)** — run a consumer over this transport
- **[Resilience](../resilience.md)** — backpressure and circuit breaker for listeners
