---
title: Configuration
description: Reference tables for MessagingConfig, EndpointDefaults, and EventSourcingConfig.
tags:
  - reference
  - configuration
---

# Configuration

The framework's two subsystems are configured through dataclasses passed to their modules:
`MessagingConfig` to `MessagingModule.register(...)` and `EventSourcingConfig` to
`EventSourcingModule.register(...)`. This page is the field reference; the feature pages explain how
each field is used.

---

## MessagingConfig

Passed to `MessagingModule.register(...)`. Every field has a default, so `MessagingModule.register()`
and `MessagingModule.register(MessagingConfig())` are equivalent.

| Option | Type | Default | Description |
|---|---|---|---|
| `global_pipeline_behaviors` | `Sequence[type[IPipelineBehavior[Any, Any]]]` | `()` | Always-run behaviors composed (outer) around every handler |
| `endpoints` | `Sequence[EndpointEntry]` | `()` | Message endpoints — local queues, external, listen (see [Routing](../features/messaging/routing.md)) |
| `routing` | `Sequence[RouteDescriptor \| ModuleRouteDescriptor]` | `()` | Route descriptors mapping message types to endpoint URIs |
| `endpoint_defaults` | `EndpointDefaults` | `EndpointDefaults()` | Per-endpoint fallback knobs; each is shadowed by an explicit per-endpoint/handler value (see below) |
| `dead_letter` | `DeadLetterConfig \| None` | `None` | Dead-letter retention, auto-replay, worker cadence (see [Error handling](../features/messaging/error-handling.md)) |
| `outbox` | `OutboxConfig \| None` | `None` | Outbox relay tuning (see [Outbox](../features/messaging/outbox.md)) |
| `inbox` | `InboxConfig \| None` | `None` | Durable inbox knobs for external listeners (see [Durable inbox](../features/messaging/inbox.md)) |
| `message_identities` | `Mapping[type[IMessage], str \| MessageIdentity]` | `{}` | Third-party type-name overrides for types you can't annotate; default path is the ClassVar |
| `audited_members` | `Mapping[type[IMessage], Sequence[str]]` | `{}` | Third-party audit-member overrides; names must be annotated fields (see [Observability](../features/messaging/observability.md)) |
| `observers` | `Sequence[type[IMessageObserver]]` | `()` | Global message observers (fire on every message incl. `invoke()`), DI-constructed at app scope |
| `transports` | `Mapping[str, TransportFactory]` | `{}` | Transport factories keyed by URI scheme; each invoked once at bootstrap |

### EndpointDefaults

Nested under `MessagingConfig.endpoint_defaults`. Each knob is a fallback, shadowed by an explicit
per-endpoint or per-handler value.

| Option | Type | Default | Description |
|---|---|---|---|
| `mode` | `EndpointMode` | `EndpointMode.BUFFERED` | Fallback mode for `local_queue` entries without an explicit `mode`; `DURABLE` makes all local queues durable |
| `error_policies` | `Sequence[ErrorPolicy]` | `()` | Fallback handler error policies; a handler's own `error_policies` shadow these per-exception |
| `sending_failure_policies` | `Sequence[SendingFailurePolicy]` | `()` | Fallback send-failure policies; a destination's own shadow these per-exception |
| `circuit_breaker` | `CircuitBreakerConfig \| None` | `None` | Fallback per-endpoint circuit breaker; an endpoint's own breaker shadows this |
| `backpressure` | `BufferingLimits \| None` | `None` | Fallback in-memory watermark for inbound listeners; a listener's own `backpressure` shadows this |
| `execution_timeout` | `timedelta \| None` | `timedelta(seconds=60)` | Default-on 60s per-handler deadline; `None` disables. Per-handler `execution_timeout` overrides |
| `max_requeue_attempts` | `int` | `5` | Fallback requeue/pause budget for `local_queue` entries without an explicit value |

---

## EventSourcingConfig

Passed to `EventSourcingModule.register(...)`. Every field has a default; the stores come from the
imported [durability backend](../fundamentals/backends.md), not from config.

| Option | Type | Default | Description |
|---|---|---|---|
| `event_serializer` | `type[IEventSerializer] \| Callable[..., IEventSerializer] \| None` | `None` | Event (de)serializer; `None` uses the default adaptix codec + upcaster chain |
| `snapshot_state_serializer` | `type[ISnapshotStateSerializer] \| Callable[..., ISnapshotStateSerializer] \| None` | `None` | Serializer for snapshot state; `None` uses the default |
| `enrichers` | `Sequence[type[IMetadataEnricher]]` | `()` | Metadata enrichers applied to appended events |
| `forwarding` | `Sequence[ForwardDescriptor]` | `()` | `forward(...)` rules bridging event-store appends onto the message bus (see [Messaging integration](../features/eventsourcing/forwarding.md)) |

---

## Further reading

- **[Messaging overview](../features/messaging/index.md)** — how `MessagingConfig` is registered
- **[Event Sourcing overview](../features/eventsourcing/index.md)** — how `EventSourcingConfig` is registered
- **[API](../reference.md)** — the generated API reference
