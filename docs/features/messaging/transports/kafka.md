---
title: 'Transport: Kafka'
description: The Kafka transport — publishing, listening, disposition mapping, group-key ordering, and the consumer group.
tags:
  - messaging
  - transport
  - kafka
  - guide
---

# Transport: Kafka

The Kafka transport is a FastStream-backed wire adapter on the same model as the
[RabbitMQ transport](rabbitmq.md). You register it on `MessagingConfig.transports` keyed by the
`kafka` scheme. The factory is `kafka_transport(url, *, consumer_group, auto_offset_reset='latest',
acks='all', enable_idempotence=False, mapper=None)`; it is imported from the broker module:

```python linenums="1"
from waku.messaging import MessagingConfig
from waku.messaging.transport.faststream.kafka import kafka_transport

config = MessagingConfig(
    transports={'kafka': kafka_transport('localhost:9092', consumer_group='orders-svc')},
)
```

---

## Publishing

Messages routed to a `kafka://…` [external endpoint](../routing.md) are persisted to the outbox and
dispatched by the relay through this transport. The relay forwards the pre-encoded body and metadata;
the transport publishes without re-serializing. See [Outbox](../outbox.md) for the endpoint and
routing wiring.

The producer defaults to `acks='all'`, so a publish is acknowledged only after every in-sync replica
has the record — required for the outbox's at-least-once guarantee, since the relay retires the
outbox row as soon as the broker acknowledges the publish. (aiokafka's own default is `acks=1`: a
leader crash before replication would silently lose an already-retired message.) This only holds when
the topic has replication factor ≥ 2 and `min.insync.replicas` ≥ 2 — on a single-replica topic
`acks='all'` degrades to a leader-only ack. Pass `acks=1` to restore leader-only acknowledgement.
`enable_idempotence=True` opts into producer→broker de-duplication under retries; it requires
`acks='all'` and is not transactional exactly-once. For full producer control, build your own
`KafkaBroker` and pass it to `FastStreamKafkaTransport(broker=...)`.

---

## Listening

`listen('kafka://<topic>')` subscribes a consumer to a topic. Each delivered message is persisted to
the [inbox](../inbox.md) before its handler runs. All subscribers on the transport share the same
`consumer_group` (see below).

```python linenums="1"
from waku.messaging import MessagingConfig, listen
from waku.messaging.transport.faststream.kafka import kafka_transport

config = MessagingConfig(
    endpoints=[listen('kafka://orders')],
    transports={'kafka': kafka_transport('localhost:9092', consumer_group='orders-svc')},
)
```

---

## Disposition map

Kafka has no broker-side requeue, so the outcomes map onto offset operations:

| Processing outcome | Broker call | Effect |
|---|---|---|
| handled successfully | `ack()` | commit the offset |
| requeue | `nack()` | seek back — the message is re-read on the next poll |
| reject (poison) | `reject()` | commit and skip |

Because a requeue is a seek-back rather than a broker redelivery, an unrecoverable poison message is
committed-and-skipped rather than blocking the partition; waku's own [dead-letter](../error-handling.md)
handling runs at the processing layer before that point.

---

## Group ordering

A message's `group_id` becomes the **Kafka message key**, so all messages sharing a group key land on
the same partition and are consumed in order. A message with no `group_id` is published without a key
and distributed across partitions. On the way in, an explicit Kafka key takes precedence over a
`group_id` header. See [Durable inbox & ordering](../inbox.md#partition-sequential-ordering) for the
per-group ordering model.

---

## Consumer group

`consumer_group` is the Kafka `group.id` shared by every subscriber on the transport. It is what makes
multiple pods **competing consumers** over the topic's partitions — distinct from the per-message
`group_id` partition key. `auto_offset_reset` (default `'latest'`) controls where a fresh consumer
group starts reading.

---

## Mapper default

Out of the box the transport uses `DefaultKafkaEnvelopeMapper`: payload in the body, Wolverine
framework fields as bare headers, and `group_id` as the message key. Override the wire format — for
interop with a foreign producer, or to reach Kafka-native fields like `partition` — by passing
`mapper=` to `kafka_transport`, or per listener via `listen(..., mapper=…)`. See
[Envelope mapper](../envelope-mapper.md).

---

## Further reading

- **[Transport: RabbitMQ](rabbitmq.md)** — the same model for RabbitMQ
- **[Outbox](../outbox.md)** — the send side: external endpoints, relay, and persistence
- **[Envelope mapper](../envelope-mapper.md)** — customize the wire format, including Kafka-native fields
- **[Durable inbox & ordering](../inbox.md)** — how `group_id` drives per-group ordering
