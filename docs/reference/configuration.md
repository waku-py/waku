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
| `leadership` | `LeadershipConfig \| None` | `None` | Opt-in cluster leader election gating durability maintenance to one node (see below) |
| `node_description` | `str` | `''` | Diagnostics label for this process's node-registry row; blank derives `'<hostname>:<pid>'` (see [Node registry](#node-registry)) |
| `message_identities` | `Mapping[type[IMessage], str \| MessageIdentity]` | `{}` | Third-party type-name overrides for types you can't annotate; default path is the ClassVar |
| `audited_members` | `Mapping[type[IMessage], Sequence[str]]` | `{}` | Third-party audit-member overrides; names must be annotated fields (see [Observability](../features/messaging/observability.md)) |
| `observers` | `Sequence[type[IMessageObserver]]` | `()` | Global message observers (fire on every message incl. `invoke()`), DI-constructed at app scope |
| `transports` | `Mapping[str, TransportFactory]` | `{}` | Transport factories keyed by URI scheme; each invoked once at bootstrap |

Durability configuration validates work and cadence values at construction. `OutboxRelayConfig`, `InboxConfig`, and
`DeadLetterConfig` require `batch_size >= 1`. Relay `recovery_interval`, `cleanup_interval`, and `stop_timeout`; inbox
`recovery_interval`, `scheduled_poll_interval`, and `stop_timeout`; dead-letter `cleanup_interval` and `stop_timeout`;
and `LeadershipConfig.stop_timeout` must all be strictly positive. Invalid values raise `ImproperlyConfiguredError`.

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

### LeadershipConfig

Nested under `MessagingConfig.leadership`. When set, exactly one node holds the `role` lease and runs
the `DurabilityMaintenanceAgent` (outbox recovery-sweep and cleanup, dead-letter auto-replay and purge,
scheduled promotion); standbys take over within one lease expiry when the holder stops renewing. When
`leadership` is `None` (the default), every node runs the maintenance agent unconditionally — the
outbox dispatch loop and inbox recovery stay node-parallel either way.

The SQLAlchemy backend requires an `engine=` argument when `leadership` is set (see
[Backends](../fundamentals/backends.md)); configuring `leadership` without it fails at startup with
`ImproperlyConfiguredError`. Lease timing is owned by the durability backend, not this config — tune it
with `SqlAlchemyBackend.register(lease_config=LeaseConfig(...))` / `MemoryBackend.register(...)` (where
`ttl_seconds` bounds failover); the coordinator consumes that same backend lease.

| Option | Type | Default | Description |
|---|---|---|---|
| `role` | `str` | `'waku:leader'` | The lease key; the `waku:` prefix is reserved for framework-owned roles |
| `stop_timeout` | `timedelta` | `timedelta(seconds=10)` | Strictly positive grace period before maintenance cancellation |

### Node registry

Every node of a durability-configured app registers itself in the cluster's membership table while it
boots, heartbeats for as long as it runs, and deregisters on clean shutdown. Registration is
unconditional and per node — it is not gated on `leadership`: a node registers because it exists, not
because it won anything. An app that can write durable rows without a backend publishing
`INodeRegistry` and `NodeRegistryConfig` (from `waku`) fails at startup with
`ImproperlyConfiguredError` — there is no time-based fallback. "Can write durable rows" is the same
condition that requires the durability stores themselves: `outbox`, `inbox`, or `dead_letter`
configured, *or* a handler whose `error_policies` move a message to the dead-letter store.

A membership transaction that fails is logged at `ERROR` and retried on the next heartbeat; the loop
is never torn down and the process is never shut down for it. A node that cannot reach the store goes
stale on the store's own clock, which is what its peers act on.

Timing is owned by the durability backend, like lease timing: tune it with
`SqlAlchemyBackend.register(node_registry_config=NodeRegistryConfig(...))` / `MemoryBackend.register(...)`.

| Option | Type | Default | Description |
|---|---|---|---|
| `heartbeat_interval` | `timedelta` | `timedelta(seconds=10)` | How often this node proves it is alive |
| `stale_after` | `timedelta` | `timedelta(seconds=60)` | Silence beyond which a node is declared dead; must be at least 3x `heartbeat_interval`, so a merely-slow node does not flap in and out of the cluster |
| `evict_interval` | `timedelta` | `timedelta(seconds=60)` | How often this node sweeps the registry for dead peers |
| `stop_timeout` | `timedelta` | `timedelta(seconds=5)` | Grace period for the membership loop to finish its tick on shutdown |

All four must be strictly positive; staleness is always evaluated with the store's clock, never the
caller's, so clock skew between nodes cannot declare a healthy node dead.

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
