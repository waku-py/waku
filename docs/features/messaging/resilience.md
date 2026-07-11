---
title: Resilience
description: Circuit breaker and backpressure — pause a failing or overwhelmed listener instead of dropping messages.
tags:
  - messaging
  - message-bus
  - resilience
  - guide
---

# Resilience

Two endpoint-level controls throttle a listener under stress rather than dropping its messages. A
**circuit breaker** pauses an endpoint whose handlers are failing at a high rate, giving a struggling
downstream time to recover. **Backpressure** pauses a broker listener whose in-memory queue is filling
faster than workers drain it. Both stop intake and resume automatically — neither discards work.

Both are separate from [error policies](error-handling.md), which decide the fate of a single failed
message. The circuit breaker reacts to an aggregate *failure rate*; backpressure reacts to *queue
depth*.

## Circuit Breaker

`CircuitBreakerConfig` tunes a rate-based breaker (no half-open probe). Over `tracking_period` it
trips when at least `minimum_throughput` messages were recorded **and** the failure fraction exceeds
`failure_rate_threshold`. On a trip the endpoint pauses for `pause_time`, then resumes and re-samples
from a clean slate — `CLOSED → OPEN (pause) → resume + reset`.

| Field                    | Type                            | Default        | Description                                                     |
|--------------------------|---------------------------------|----------------|-----------------------------------------------------------------|
| `failure_rate_threshold` | `float`                         | `0.2`          | Failure fraction in `(0.0, 1.0]` that trips the breaker         |
| `tracking_period`        | `timedelta`                     | `5 minutes`    | Rolling window over which failures are counted                  |
| `minimum_throughput`     | `int`                           | `10`           | Minimum messages in the window before the breaker can trip      |
| `pause_time`             | `timedelta`                     | `5 minutes`    | How long the endpoint pauses after a trip                       |
| `track_exceptions`       | `tuple[type[Exception], ...]`   | `()`           | Exception types that count as failures (empty = all)            |
| `ignore_exceptions`      | `tuple[type[Exception], ...]`   | `()`           | Exception types excluded from the failure count                 |

Attach a breaker per endpoint, or set a fallback for every endpoint via
`endpoint_defaults.circuit_breaker`:

```python linenums="1"
from waku.messaging import (
    CircuitBreakerConfig,
    EndpointDefaults,
    MessagingConfig,
    listen,
    local_queue,
)

breaker = CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=20)

config = MessagingConfig(
    endpoints=[
        listen('rabbitmq://orders', circuit_breaker=breaker),  # pauses the broker listener on trip
        local_queue('projections', circuit_breaker=breaker),   # pauses this queue's worker on trip
    ],
    endpoint_defaults=EndpointDefaults(circuit_breaker=breaker),  # fallback for endpoints without one
)
```

A breaker on a `local_queue` pauses that queue's processing worker; a breaker on a `listen(...)`
inbound listener pauses the broker listener instead — messages already buffered still drain, but no
new ones are pulled until the pause elapses.

## Backpressure

`BufferingLimits(high, low)` is an in-memory watermark for inbound listeners. When the post-enqueue
depth reaches `high` the broker listener is stopped; when it falls back to `low` it resumes. This
paces intake to worker throughput — it never pauses processing, only the broker pull. `low` must
satisfy `0 <= low < high`.

```python linenums="1"
from waku.messaging import BufferingLimits, EndpointDefaults, MessagingConfig, listen

limits = BufferingLimits(high=500, low=100)

config = MessagingConfig(
    endpoints=[listen('rabbitmq://orders', backpressure=limits)],
    endpoint_defaults=EndpointDefaults(backpressure=limits),  # fallback for listeners without one
)
```

!!! note "One gate, two triggers"
    On an inbound listener the circuit breaker and the watermark share a single refcounted gate over
    the broker subscription. The breaker resumes on a timer, the watermark resumes on depth — and
    neither trigger lifts the other's pause. The listener resumes only once both are clear.

## Further reading

- **[Error Handling](error-handling.md)** — per-message retry, dead letter, and replay
- **[Routing & Endpoints](routing.md)** — endpoint types, modes, and where breakers attach
- **[Dedicated Consumer](dedicated-consumer.md)** — a standalone listener process with full resilience
