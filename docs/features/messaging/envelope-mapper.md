---
title: Envelope Mapper
description: Pluggable per-broker wire format control for Kafka and RabbitMQ transports.
tags:
  - messaging
  - transport
  - kafka
  - rabbitmq
  - interoperability
  - guide
---

# Envelope Mapper

Each FastStream transport ships a pluggable **envelope mapper** that owns the wire format in both
directions: outgoing payload serialization and header projection, and incoming header reconstruction
into `EnvelopeMetadata`. Swapping the mapper lets a single transport interoperate with a foreign
producer or consumer without touching the rest of the message bus.

## Default wire format

The built-in `DefaultKafkaEnvelopeMapper` and `DefaultRabbitEnvelopeMapper` implement
Wolverine's two-tier header layout:

- **Payload** — the full envelope body is the message value.
- **Envelope headers** — framework fields (`message_id`, `correlation_id`, `causation_id`,
  `message_type`, `message_version`, `content-type`, `timestamp`, `scheduled_time`, `expires_at`,
  `group_id`) are written **bare** (no prefix) into broker headers.
- **User headers** — arbitrary headers from `EnvelopeMetadata.headers` are copied bare, with one
  constraint: any key that collides with a reserved framework name is **silently dropped** (reserved
  wins). This is the interop contract — a consumer relying on a bare `correlation_id` header will
  always see the Waku framework value, never a user-supplied one.
- **Content-type awareness** — all outgoing messages carry `content-type: application/json`.
  Incoming messages with a different content-type raise `UnsupportedContentTypeError` and are
  rejected (no requeue). Multi-codec negotiation is deferred to M4+.

The mapper helpers are importable directly if you need them in a custom mapper:

```python
from waku.messaging.transport.mapping import wire_headers_of, metadata_from_headers
```

## Mapper interfaces

```
IEnvelopeMapper[TIncoming, TOutgoing]          # generic base (ABC)
  └─ IKafkaEnvelopeMapper                      # KafkaMessage → KafkaOutgoing
  └─ IRabbitEnvelopeMapper                     # RabbitMessage → RabbitOutgoing
```

`map_outgoing(payload, metadata) -> TOutgoing` is called by `send()` on every publish.
`map_incoming(msg) -> (payload, metadata)` is called by the inbound subscriber before dispatch.

`KafkaOutgoing` and `RabbitOutgoing` are frozen dataclasses. The core fields (`body`, `key`,
`headers` for Kafka; `body`, `headers` for Rabbit) are always forwarded to `broker.publish`.
Each struct also carries **native passthrough fields** — set them in a custom mapper to reach
broker capabilities the Wolverine wire format does not expose:

**`KafkaOutgoing` native fields** (all default `None`, omitted from publish when `None`):

| Field | Type | FastStream kwarg |
|---|---|---|
| `partition` | `int \| None` | `partition` |
| `timestamp_ms` | `int \| None` | `timestamp_ms` |
| `correlation_id` | `str \| None` | `correlation_id` (FastStream request-reply, not the Waku envelope field) |
| `reply_to` | `str \| None` | `reply_to` |

**`RabbitOutgoing` native fields** (all default `None`, omitted from publish when `None`):

| Field | Type | FastStream kwarg |
|---|---|---|
| `correlation_id` | `str \| None` | `correlation_id` (AMQP property, not the Waku envelope header) |
| `reply_to` | `str \| None` | `reply_to` |
| `priority` | `int \| None` | `priority` |
| `expiration` | `int \| float \| datetime \| timedelta \| None` | `expiration` (seconds, or datetime/timedelta) |

## Writing a custom mapper

Subclass the broker-specific ABC and implement both abstract methods. The example below bridges a
Kafka topic where a legacy producer encodes messages as `{"event": <type>, "data": <payload>}`
and sets a reply topic for request-reply flows:

```python linenums="1"
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.messaging.transport.faststream.kafka import (
    IKafkaEnvelopeMapper,
    KafkaOutgoing,
)
from waku.messaging.transport.interfaces import EnvelopeMetadata
from waku.messaging.transport.mapping import metadata_from_headers, wire_headers_of


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderPlaced:
    order_id: str


class LegacyKafkaMapper(IKafkaEnvelopeMapper):
    """Maps the legacy {"event": ..., "data": ...} wire format.

    Sets ``reply_to`` so the broker knows where to route request-reply responses.
    """

    REPLY_TOPIC = 'orders.replies'

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> KafkaOutgoing:
        wire_body = {'event': metadata.message_type, 'data': payload}
        return KafkaOutgoing(
            body=wire_body,
            key=metadata.group_id.encode() if metadata.group_id else None,
            headers=wire_headers_of(metadata),
            reply_to=self.REPLY_TOPIC,   # threaded to KafkaBroker.publish(reply_to=...)
        )

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raw: dict[str, Any] = await msg.decode()
        payload: dict[str, Any] = raw.get('data', raw)
        metadata = metadata_from_headers(dict(msg.headers or {}))
        return payload, metadata
```

The same pattern applies for RabbitMQ — subclass `IRabbitEnvelopeMapper`, return a
`RabbitOutgoing` with the native fields you need:

```python linenums="1"
from waku.messaging.transport.faststream.rabbitmq import (
    IRabbitEnvelopeMapper,
    RabbitOutgoing,
)


class PriorityRabbitMapper(IRabbitEnvelopeMapper):
    """Publishes all messages at AMQP priority 5."""

    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> RabbitOutgoing:
        return RabbitOutgoing(
            body=payload,
            headers=wire_headers_of(metadata),
            priority=5,
        )

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        payload = await msg.decode()
        return payload, metadata_from_headers(dict(msg.headers or {}))
```

## Plugging in a custom mapper

### Per-scheme (transport-level default)

Pass `mapper=` to `kafka_transport` or `rabbit_transport`. Every subscriber and publisher on
that transport uses the mapper unless overridden at the endpoint level.

```python linenums="1"
from waku.messaging import MessagingConfig, MessagingModule, listen
from waku.messaging.transport.faststream.kafka import kafka_transport

MessagingModule.register(
    MessagingConfig(
        endpoints=[listen('kafka://orders')],
        transports={'kafka': kafka_transport(
            'localhost:9092',
            consumer_group='orders-svc',
            mapper=LegacyKafkaMapper(),
        )},
    )
)
```

### Per-endpoint (listener-level override)

Pass `mapper=` to `listen()` to override the transport-level default for a single topic or queue.
Useful when one service consumes from multiple topics with different wire formats.

```python linenums="1"
from waku.messaging import MessagingConfig, MessagingModule, listen
from waku.messaging.transport.faststream.kafka import (
    DefaultKafkaEnvelopeMapper,
    kafka_transport,
)

MessagingModule.register(
    MessagingConfig(
        endpoints=[
            listen('kafka://orders'),            # uses LegacyKafkaMapper
            listen(                              # overrides to Wolverine format
                'kafka://internal-events',
                mapper=DefaultKafkaEnvelopeMapper(),
            ),
        ],
        transports={'kafka': kafka_transport(
            'localhost:9092',
            consumer_group='orders-svc',
            mapper=LegacyKafkaMapper(),         # default for all listeners
        )},
    )
)
```

## Forward pointers

- **CloudEvents (gap #24)** — the mapper seam is the integration point for CloudEvents envelopes;
  a `CloudEventsKafkaMapper` will implement this interface without changes to the transport or bus.
- **Multi-codec negotiation** — content-type-driven codec selection (e.g. Avro, Protobuf) is
  deferred to M4+; the mapper seam is designed to support it.

## Further reading

- **[Outbox & Transport](outbox.md)** — transactional outbox, relay, and transport setup
- **[Routing & Endpoints](routing.md)** — how messages are routed to transports
- **[Dedicated Consumer](dedicated-consumer.md)** — running a consumer-only Waku node
