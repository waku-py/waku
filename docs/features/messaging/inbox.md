---
title: Durable inbox & ordering
description: The durable-inbox model — deduplication, the claim-and-drain loop, and partition-sequential ordering.
tags:
  - messaging
  - inbox
  - durability
  - explanation
---

# Durable inbox & ordering

The **inbox** is the durable store behind every DURABLE local queue and every external
[listener](transports/rabbitmq.md). It is what makes durable delivery at-least-once and per-group
ordering possible: a message is written to the inbox *before* it is processed, and only removed once
its handler has finished. This page explains that model. To wire an inbox into a running consumer,
see [Dedicated consumer node](dedicated-consumer.md#inbox-store-setup).

---

## Deduplication

Each message is persisted as one inbox row **per handler**, keyed by the composite
`(message_id, handler)` — where `handler` is the handler's fully-qualified name. Persistence happens
before the message is enqueued for processing, so the row exists before any work is attempted.

That composite key is how a **redelivery of the same frame** is suppressed: if the same persisted
message is delivered again — a broker redelivery, or a competing consumer picking up a row the
original owner never finished — the duplicate insert is a no-op for handlers that already have a row.
This is a redelivery safeguard, not business idempotency: two independent sends mint different
`message_id`s and are two distinct messages. Durable handlers must still be idempotent — see
[Runtime & delivery semantics](runtime.md#at-least-once-and-the-handler-idempotency-contract).

---

## The claim-and-drain loop

Workers process inbox rows by claiming them with `FOR UPDATE SKIP LOCKED`:

- A worker claims a batch of pending rows. `SKIP LOCKED` means two workers — or two pods against one
  database — never grab the same row, so you scale out by running more consumers with no coordination.
- The executor dispatches each claimed row in its **own** scope per attempt, never inside the claim
  transaction, so a slow handler does not hold the claim lock.
- On success the row is finalized (deleted, or kept briefly for dedup); on failure it escalates
  through the [error policy](error-handling.md).

If a worker dies mid-flight, the rows it persisted but never finalized are reclaimed by **crash
recovery** on restart and re-dispatched — again under `FOR UPDATE SKIP LOCKED`, so recovery is
concurrency-safe by construction. There is **no leader election**; every pod runs its own recovery.

---

## Partition-sequential ordering

Ordering is per group. A message carries a **group key** when you set `group_id` on it (via
[Delivery options](delivery-options.md)) or configure a `partition_by` extractor on the endpoint:

- **Keyed** messages are assigned a sequence number and drained **head-of-queue** — strict FIFO
  within the group. The number is allocated by an `ISequenceAllocator`
  (`waku.messaging.partition.ISequenceAllocator`), which you provide. It must allocate the next
  number in the **same transaction** as the row insert, so the sequence is co-committed and ordering
  holds across pods sharing one database.
- **Keyless** messages bypass sequencing and are processed in parallel with **no ordering
  guarantee**.

```python linenums="1"
from waku.messaging import listen

# Order every message whose customer_id matches, in arrival order per customer.
endpoint = listen('rabbitmq://orders', partition_by=lambda msg: msg.customer_id)
```

Per-group order holds within one consumer. Plain competing consumers across pods spread a group's
messages, so preserving order at scale needs a single-active-consumer queue or a consistent-hash
exchange — see the scale-out note on [Dedicated consumer node](dedicated-consumer.md#scale-out).

---

## Further reading

- **[Runtime & delivery semantics](runtime.md)** — delivery guarantees and the durable transaction model
- **[Dedicated consumer node](dedicated-consumer.md)** — wiring and running a durable consumer
- **[Error handling](error-handling.md)** — how a failed inbox attempt escalates
- **[Outbox](outbox.md)** — the send-side counterpart for outbound messages
