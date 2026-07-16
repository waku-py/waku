# Waku — Critter Stack for Python Roadmap

> **Goal:** Evolve Waku into a Python alternative for the Marten + Wolverine (.NET) stack
> **Last updated:** 2026-07-13
> **Current version:** M3 in progress on `feat/messaging-transport` (nothing pushed — dex reviews + pushes) — M2 complete (Consolidation Gate DONE). M3 gaps #16 (handler timeout), #17 (pause-on-failure + REQUEUE), #18 (DeliveryOptions + `schedule_send` + scheduled/expiration), #22 (global durability default) DONE. OpenTelemetry **accepted, sequenced post-fix-program** (dex 2026-07-13; conditional `IMessageObserver` via `MessagingConfig.observers` + `traceparent` in headers + `waku[otel]` extra — memory `project_otel_optin_design`, spec pass before build). Inbox Listener + Transport Collection COMPLETE: in-process listener (`a29a744`), Slice C separate-process consumer (`c491f8b`), Slice B backpressure/gap #2 (`23df594`), T2 Kafka adapter (`e7b3b0e`) — Rabbit + Kafka, Wolverine-parity. **Envelope mapper parity (gap #23) ✅ DONE (`579099e`)** — pluggable per-transport `IEnvelopeMapper` + envelope decomposition. **Unified bidirectional Endpoint model ✅ DONE (`95cdb2b`)** — collapsed `config.inbound` into ONE URI-keyed Endpoint (send+listen merge per URI); per-URI mapper/`partition_by` single home; #20/#24 now have a home by construction. **gap #20 (structured logging + `bus.invoke()` observability + endpoint destination + config-declared & per-endpoint observer registration) ✅ DONE (`b582a5a`).** **Parity gap triage (2026-07-04):** the 28 untracked audit gaps (`.research/2026-07-03-holistic-audit-report.md` §3.4) categorized into M3 / M4+ / Declined as gaps #25–#45 — see the *Parity Gap Triage (2026-07-04)* subsection under M3.
>
> **Post-audit fix queue — EXECUTING since 2026-07-10** (21 slices; per-slice status lives in `.research/2026-07-04-execution-sequence.md` §0.5 manifest + §0.6 log, deliberately NOT duplicated here). **PROGRAM COMPLETE — 21/21 ✅** (`2aeb5dd`…`d684d16`, 3920 tests / 98.67%, checker 0 violations, import-linter 12/12, unpushed): outbox dedup per `(idempotency_key, destination)` so multi-destination fan-out survives · fail-loud ES event forwarding · `StoredEvent` provenance passed to `transformed_to` · cluster-wide per-group FIFO under concurrent relays (A1) · durable projection SKIP checkpoints + TOCTOU-safe gap detection (default ON) · correlation/causation ids widened to free-form `str` · most-derived `TransactionalBehavior` honored at the framework position · per-endpoint `default_*` knobs nested into `MessagingConfig.endpoint_defaults: EndpointDefaults` (**breaking**) · one `EndpointExecutorFactory` construction seam · per-rule requeue/pause budgets on `ErrorPolicy` · null objects for optional durability collaborators (`PassthroughCircuitBreaker`, `DiscardingDeadLetterStore`) · parity polish (**BUFFERED terminal DLQ, `schedule_publish`, correlation auto-wire, `delete_stream`→`archive_stream`**) · `IEvent`/`MessageIdentity` single import home in `waku.messages` (**breaking**) · mechanical hygiene (`timedelta` `stop_timeout`, kafka export, symbol homes) · **M8 `ListeningAgent`/`ListeningStatus`** (own the listen half per URI in `endpoints/_internal/`; god-module `_wire_listeners` deleted; deferred-capability ledger below) · test hygiene (wall-clock sleeps removed) · **docs accuracy + completeness + re-cut — the docs arm is COMPLETE** (Candidate-B concern-lifecycle nav: 7 top-level groups, 49 user-facing pages, 10 new pages incl. `messaging/runtime.md`, `messaging/transports/{rabbitmq,kafka}.md`, `reference/configuration.md`, `mediator.md`; 2 orphan indexes deleted; ZERO file moves — grouping is nav-side only) · **visibility migration** (`c2d5935`, **breaking**: Candidate-C tree — narrow `_internal/` subpackages, SPI = subpackage facades, one blessed import home per symbol; `scripts/check_visibility.py` ghost-killer ARMED in `task check`, 217 incoming violations → 0; tree map in `CLAUDE.md`). **Slice 21 durability backend assembly ✅ DONE (2026-07-12, breaking: per-technology backends `waku.backends.{sqlalchemy,memory,testing}`; six `.store` config fields deleted; `waku.messaging.durability` + `IEventStore` facets; session-identity validation deleted — QUEUE FULLY COMPLETE 21/21).** **Tail — #22 post-queue hygiene ✅ DONE** (`125e267`, 3922 tests / 98.67%, **breaking**): `MessageRegistry` deleted, `HandlerMap` surfaced on the `waku.messaging` facade; `IBehaviorPolicy.behaviors_for` takes `handler_map: HandlerMap` (an honest type for the handler graph a policy is handed — Wolverine hands policies the whole `HandlerChain` list; the bridge's `object` widening is gone); the map is frozen BEFORE it is DI-registered, so the public read-surface is fail-loud by construction (pinned by `tests/messaging/pipeline/test_handler_map_frozen_before_policies.py`); `custom-extensions.md` rewritten onto the blessed API.
>
> **Backend-customization boundary — ✅ RESOLVED (slice #23, 2026-07-12, breaking):** (a) **custom-store escape hatch = whole-backend COMPOSITION** — the one customization boundary is the backend module; `waku.di` gains NO override surface, no `overrides=` kwarg; the author contract (R1–R7) + `AcmeBackend` worked example + provider classification table live on `docs/fundamentals/backends.md`; `ImplicitOverrideDetectedError` stays raw as the two-backends guard (pinned by `tests/backends/test_duplicate_backend_fail_loud.py`). (b) **R4 — backend-owned sequencing LANDED**: `waku.messaging.sqla` deleted; `SqlAlchemySequenceAllocator` + `SequenceTables`/`bind_sequence_tables` (renamed) single-homed at `waku.backends.sqlalchemy` (new `sequence/` subpackage; public `column_types.py` leaf keeps `EnumFromValues`, `EnumFromKeys` deleted); both backends provide `ISequenceAllocator` statically (sqla scoped, memory singleton `InMemorySequenceAllocator`); `_SequenceAllocatorValidationExtension` + `_AbsentSequenceAllocator` deleted, replaced by ONE registration-time check (`_require_sequence_allocator_when_active`: inbox-active OR `partition_by` ⇒ allocator provided); `SequenceAllocatorContract` joined the conformance kit; pg suites relocated to `tests/backends/sqlalchemy/*` with `pg_session_for`. Revises `decision_m2b2_partition_sequential` (allocator is no longer user-provided). Next feature gap: #24 (CloudEvents).

---

## Parity Tracker

| Milestone | Critter Stack Parity | Status |
|-----------|---------------------|--------|
| Pre-M1    | ~30%                | Done   |
| M1a       | ~45%                | Done   |
| M1b       | ~65%                | Done   |
| M2a       | ~70%                | Done   |
| M2b       | ~75%                | Done    |
| M2c + M2d | ~88%                | Core complete (DLQ + inbox crash-recovery + Sending Failure Policies + **Circuit Breaker** done; **Backpressure** done (Slice B); transport collection (Rabbit + Kafka) done; **OTel accepted (post-fix-program)**) |
| M2e       | ~88%                | Done    |
| M3        | ~92%                | In progress (#16/#17/#18/#22/#23 done; Inbox Listener + backpressure #2 + transport collection Rabbit+Kafka done; **Unified bidirectional Endpoint model done**; #20 structured logging + invoke-observability DONE) |
| M4+       | ~97%                | Future |

**M2 is split into sub-milestones** — see `.research/m2-spec-review.md` for rationale.
M2a is a hard prerequisite for everything else; M2b/M2c/M2d can run in parallel. **M2e** (ES↔messaging event forwarding) was added after the integration review (`.research/m2-plan-update-tracker.md`) and depends on M2b.1 + M2a.5.

**⚠ These %% are PLAN-RELATIVE (completion-against-plan), not coverage-against-reference.** The 2026-06-20 Critter-parity
audit (`.research/2026-06-20-critter-parity-review.md` — 203 capabilities across 14 domains, adversarially verified)
measured true capability coverage at **~65-75%** (2026-06-20 figure — **superseded 2026-07-13, see below**), not 88-90%: only 63/203 covered as of the 2026-06-20 audit (gaps #16/#17/#18/#22 completed since), with **51 untracked blind-spots**
+ 26 partials + 63 already-tracked known-gaps + 13 confirmed-sound divergences. Blind-spots sit *outside* the plan, so they
cannot lower a plan-relative %% — which is exactly why the parity audit was run. The 6 highest-impact blind-spots from the
main audit are gaps #16-21; the serialization supplement added gaps #22-24. **Gaps #16/#17/#18/#22 are now done.** The
full ranked inventory + divergence ledger is in the audit doc.

**Recomputed 2026-07-13** (pre-1.0 gate audit, `.research/2026-07-13-pre-1.0-gate-audit.md` §5 — 103 agents, 48
confirmed findings): weighted TRUE parity ≈ **62%** (unweighted mean **59%**) — this **replaces** the June ~65-75%
estimate. The number moved DOWN not because code regressed but because this pass depth-audited the two
flagship-weighted weak domains. Per-domain (weight): messaging-core **75%** (5), durable-messaging **80%** (4),
transports **42%** (3), event-sourcing **48%** (4), ES↔messaging glue **65%** (2), observability **45%** (2). Domains
1-2 — what every 1.0 user touches first — sit at 75-80% with all remaining gaps additive (Sagas, batching, remote
request/reply, conventional discovery; store breadth, leader election, multi-tenancy). **Divergence ledger: 10 total —
9 intentional** (each memory- or docstring-backed; re-proposals are non-findings) **+ 1 accidental** —
CircuitBreakerConfig defaults vs the 'Wolverine-faithful' claim, **FIXED in `64d88a7`** (`failure_rate_threshold`
0.2→0.15, `tracking_period` 5→10 min; the 250 ms sub-period smoothing stays a documented divergence).

---

## Pre-1.0 Gate

Framing (dex, 2026-07-13; widened same day by the all-levels directive — «плохих решений по архитектуре/коду не
должно быть ни на одном уровне»). **1.0 is gated on the fix-program slices #24–#33**: the class-A "hard-to-revoke"
set from the pre-1.0 gate audit (`.research/2026-07-13-pre-1.0-gate-audit.md`: public API/SPI, wire format, DB
schema, config shape, guarantee semantics), the guarantee-defect groups (#27/#28), and the FULL quality-B backlog
(#29–#33 — 35 items grouped by domain: messaging-correctness / spi-observability-substrate /
eventsourcing-correctness / backends-kit-hygiene / modules-di-harness) plus the audit's uncovered-zones follow-up
sweep. Only **feature-absence** parity items (see the Parity Tracker note above) are **additive post-1.0 feature
tracks, NOT release blockers**.
*(These are fix-program slice numbers continuing #21/#22/#23 — distinct from the parity `gap #NN` series; `gap #24` =
CloudEvents, unrelated to slice #24.)*

| Slice | Audit items | Scope | Status |
|-------|-------------|-------|--------|
| **#24 — Mechanical fixes** | A-3, A-4, A-5, A-6 · B-11, B-38 · CB defaults | timedelta retry durations; `metadata_`→`metadata` column unification; `bind_*_tables` wrapper convention; `*Error` exception names; `DuplicateErrorPolicyError` strict wiring; `functools.cache` metadata leak; Wolverine-aligned CB defaults | ✅ DONE (`64d88a7`) |
| **#25 — Rabbit persistence** | A-1 | `FastStreamRabbitTransport` publishes NOT_PERSISTENT → persist-follows-durability on the `RabbitOutgoing` mapper SPI VO (silent at-least-once loss on broker restart) | In flight |
| **#26 — Decider snapshot** | A-2 | `SnapshotDeciderRepository` fixes `state_type` at construction → union-typed state silently reset-to-not-created on snapshot reload | In flight |
| **#27 — Cascading per-destination** | B-5 | `_is_durable` classifies at message-TYPE not per-destination → non-durable subscriber acts on a rolled-back cascade | Planned |
| **#28 — DLQ replay / conformance kit** | B-9, B-10, B-28, B-29 | dead letters structurally unreplayable (dropped fields); dual-origin `destination` fails `endpoint_for()`; memory-backend DLQ silently empty; + dead-letter conformance-kit facet symmetry | Planned |

| **#29 — Messaging correctness** | B-1..B-4, B-6..B-8, B-15 | mapper/scheme validation; buffered per-handler requeue budget; relay try-split; poison UUID; finalize UoW scope; SCHEDULED invariant; stale comments; broker-before-relay by construction | Plan in progress |
| **#30 — SPI/observability/substrate** | B-12..B-14, B-40, B-42 | dead `fetch_pending` twin; keyless docstring contract + kit case; audit heading collision; backoff overflow; `transaction.py` placement | Plan in progress |
| **#31 — Event-sourcing correctness** | B-16..B-26 | projection failure isolation; lease invariant; snapshot failure boundary + live-reference; `save()` drain-after-append; upcaster-conflict identity; `assert_never`; `is_abstract` generics | Plan in progress |
| **#32 — Backends/kit hygiene** | B-27, B-30..B-33 | one construction authority for `SqlAlchemyEventStore`; stale kit comments; integration-handler dedup | Plan in progress |
| **#33 — Modules/DI/harness** | B-34..B-37, B-39, B-41 | parent-mapping multiplicity; cycle detection; dead traverse; harness copy-on-override; `__aenter__` exception safety | Plan in progress |

**B-backlog (audit §3): ALL 35 quality items pulled into the gate** (slices #29–#33, per the all-levels directive) on
top of the seven already riding #24/#27/#28 (**B-11 / B-38** landed in #24; **B-5 / B-9 / B-10 / B-28 / B-29** in
#27/#28). Only the audit's *feature-absence* list stays post-1.0. All 6 class-A items sit in slices #24–#26.
Named follow-ups owned by the program (not yet sliced): invoke(event) non-durable cascade leg flushes pre-commit
(depth-aware `DeferredCascadingBehavior`, out of #27 by design — rides #29's messaging-correctness domain or its own
micro-slice); kit gaps `promote_due_scheduled` + `read_all` pagination (ride the tst1-amendment coverage audit in #28).

---

## Completed

### Event Sourcing (Pre-M1)

Full event sourcing with both OOP aggregates and functional deciders.

- Aggregates with `apply()` / `_when()` pattern
- Decider pattern (functional event sourcing)
- Event store (SQLAlchemy/PostgreSQL)
- Projections with lock-based processing
- Snapshots (with SQLAlchemy adapter)
- Upcasting (`@upcast` decorator, rename/add/remove field helpers)
- Event type registration (`EventType` / `EventTypeSpec`)

### M1a — Unified Bus & Routing (Phases 1-3, Tasks 1-16) [DONE]

Foundation for modular monolith messaging.

- [x] Message envelope with correlation/causation metadata
- [x] MessageContext (ContextVar-based, read-only)
- [x] EnvelopeFactory
- [x] Unified handler model (`MessageHandler` → `RequestHandler` / `EventHandler`)
- [x] HandlerMap (unified message→handler registry)
- [x] MessageDispatcher (handler/behavior resolution, routing-free)
- [x] PipelineExecutor with behavior chaining
- [x] Endpoint model (`Endpoint` ABC, `EndpointEntry` union)
- [x] LocalQueueEndpoint (anyio memory streams, background worker)
- [x] MessageRouter + RoutingTable
- [x] `route()` / `route_module()` helpers
- [x] EndpointLifecycleExtension
- [x] IUnitOfWork protocol + SqlAlchemyUnitOfWork
- [x] TransactionalBehavior

| Doc | Path |
|-----|------|
| Design | `docs/plans/_archive/2026-03-09-messaging-critter-stack-design.md (archived)` |
| Implementation plan | `docs/plans/_archive/2026-03-10-messaging-implementation-plan.md (archived)` |
| Target state | `docs/plans/_archive/2026-03-24-messaging-target-state.md (archived)` |
| Gap analysis | `docs/plans/_archive/2026-03-10-messaging-critter-stack-gap-analysis.md (archived)` |
| Wolverine architecture | `docs/plans/wolverine-architecture-research.md` |
| Wolverine endpoints | `docs/plans/wolverine-endpoint-architecture.md` |
| Wolverine routing | `docs/plans/wolverine-message-routing-research.md` |

### M1b — External Transports & Durability (Phases 4-7, Tasks 17-32) [DONE]

Extends to microservices with at-least-once delivery.

- [x] JsonEnvelopeSerializer with type registry
- [x] ExternalEndpoint with outbox dispatch
- [x] Outbox store (IOutboxStore protocol + SQLAlchemy)
- [x] OutboxRelay with adaptive polling + backoff
- [x] OutboxMessage model with full lifecycle (PENDING → PROCESSING → DISPATCHED / FAILED / DEAD_LETTERED)
- [x] Error policies — ErrorPolicy builder API (`on_exception(...).retry_with_backoff(...).then_move_to_dead_letter()`; shipped as `RetryPolicy` early, renamed to `ErrorPolicy` in M2a)
- [x] ErrorPolicyEvaluator (endpoint-level)
- [x] EndpointExecutor (scope-per-attempt retry loop)
- [x] Dead letter store (IDeadLetterStore + SQLAlchemy)
- [x] ITransport protocol
- [x] FastStream transport adapter
- [x] Stuck message recovery
- [x] Outbox cleanup

| Doc | Path |
|-----|------|
| Error handling arch | `.research/dead-letter-final-architecture.md` |
| Transaction analysis | `.research/transaction-architecture-analysis.md` |
| Outbox config design | `.research/outbox-config-api-design.txt` |
| Outbox relay/DLQ refactor | `.research/plan-outbox-relay-dead-letter-refactor.md` |
| Wolverine dead letter | `.research/wolverine-dead-letter-relay.md` |
| Local queue lifecycle | `.research/local-queue-stream-lifecycle-analysis.md` |

---

## M2 — Production Readiness [CORE COMPLETE — OTel accepted (post-fix-program), Backpressure DONE (Slice B); split into M2a–M2e]

Originally six interlocking features. Split into four sub-milestones after Wolverine gap analysis
(`.research/wolverine-gap-analysis.md`). M2a is a hard prerequisite; M2b/c/d can run in parallel.

| Doc | Path |
|-----|------|
| Design (M2a + M2b, approved) | `docs/plans/2026-04-12-m2-messaging-durability-design.md` |
| Wolverine gap analysis | `.research/wolverine-gap-analysis.md` |
| Spec review + scoping | `.research/m2-spec-review.md` |

---

### M2a — Core Semantics [DONE] (blocks M2b/c/d)

Foundation for per-handler configuration and endpoint tuning. Sequential; lands first.

#### Message Type Identification

Stable wire names for persistent storage and cross-service messaging.

- [x] Bidirectional `str ↔ type` registry via `MessagingConfig.message_identities`
- [x] Naming chain (explicit alias → FQN fallback)
- [x] Migrate EnvelopeFactory + JsonEnvelopeSerializer to use naming chain
- [x] `MessageIdentity(name, version)` value object for versioned aliases
- [ ] ~~Content-type versioning (`application/vnd.{alias}.v{N}+json`)~~ — moved to M3 Message Versioning & Forwarding (spec §1 does not define content-type emission)

| Doc | Path |
|-----|------|
| Research | `.research/message-type-identification.md` |
| Decision | Memory: `decision_message_type_naming.md` |

#### Error Policy Refactor (handler failures)

Rename `RetryPolicy → ErrorPolicy`. Per-handler policies via the `Handler.error_policies` ClassVar (zero-kwarg `bind`), ordered escalation `stages` (retry → backoff → DLQ).

- [x] Rename + new builder API (`ErrorPolicy.on_exception(...).retry_with_backoff(...)`)
- [x] Optional `when=` predicate for fine-grained matching
- [x] Per-handler `error_policies` ClassVar on the handler class (NOT a `bind()` kwarg; `bind` stays zero-kwarg), MRO inheritance
- [x] Global fallback policies via `MessagingConfig.endpoint_defaults.error_policies`
- [x] `ErrorPolicyRegistry` resolution: per-handler → global → none
  - Within a list: specific predicate > type-only > `on_any_exception`
- [x] Scope: handler execution failures only (outbound `ITransport.send()` → M2d `SendingFailurePolicy`)

#### Endpoint Modes & Concurrency

Explicit processing mode enum + per-endpoint parallelism.

- [x] `EndpointMode` enum (INLINE / BUFFERED / DURABLE) — INLINE local-only in M2; external INLINE deferred to M3
- [x] `InlineEndpoint` — minimal wrapper, no queue / no worker / no stream lifecycle
- [x] `max_parallel` per endpoint via `anyio.CapacityLimiter`
- [x] `pause()` / `resume()` on `Endpoint` ABC (no-op default)
- [x] `MemoryStreamWorker` extraction (shared between buffered + durable)
- [x] Validation: `EndpointMode.DURABLE` requires `config.inbox`

#### Cascading Messages (OutgoingMessages)

Scoped DI collector for handler-produced messages. `CascadingBehavior` (pipeline behavior, runs in the handler's scope) flushes after the handler. Design B — async, isolated, post-commit; each cascade runs in its own scope/tx.

Cascade durability is **per-endpoint** (D1): it follows the destination endpoint — a durable endpoint takes the outbox (atomic, in-tx), a non-durable endpoint is post-commit fire-and-forget. When no outbox is configured, all cascades are post-commit fire-and-forget (`CascadingBehavior`).

- [x] `OutgoingMessages` with frame-based nesting
- [x] `IOutgoingMessages` (handler Protocol: send/publish) + `IOutgoingMessagesFrames` (framework Protocol: frame lifecycle) via `scoped(AnyOf[...])`
- [x] `CascadingBehavior` (auto-registered pipeline behavior, outermost; collect+flush in handler scope — NOT on the bus, which stays unchanged)
- [x] Correlation propagation (parent → child via MessageContext); `group_id` propagation deferred to M2b.2
- [x] `OutboxCascadingBehavior` (M2b.1) — inside-tx variant, atomic outbox write; config-selected, replaces `CascadingBehavior` when outbox set

| Doc | Path |
|-----|------|
| Research | `.research/wolverine-cascading-messages.md` |
| Design | `docs/plans/2026-03-22-cascading-messages-design.md` |
| Decision | Memory: `decision_cascading_messages.md` |
| API decisions | `.research/m2-api-surface-decisions.md` |

---

### M2a.5 — Inline event invocation (same-tx domain events) [DONE] (after M2a.4)

Extends `invoke` to events so domain-event handlers can run inline, in the caller's scope/transaction, atomically — the correct home for same-tx domain events (eShop/de la Torre pattern; inline same-tx fan-out, modeled on Wolverine's combine semantics — but handler ORDER and the event/request API distinction are deliberate Waku divergences (Wolverine is silent on combined-handler order and has a single `InvokeAsync`, not separate event/request verbs)). Distinct from `publish` (async, isolated, post-commit) and cascade (Design B). Resolves the two-axes model: `invoke` = inline+same-tx; `publish`/`send`/cascade = deferred+own-tx.

- [x] `invoke(event) -> None` — fan-out to ALL local handlers, inline, in the caller's scope/transaction
- [x] Handler failure propagates (rolls back the originating transaction) — atomic consistency boundary
- [x] Keep `invoke(request) -> TResponse` (single handler) — same `invoke` concept ("execute now in my context")
- [x] Same-tx coupling is EXPLICIT (you call `invoke`); never automatic on `publish`
- [x] Docs: when to use `invoke(event)` (same invariant boundary) vs `publish(event)` (eventual reaction)

| Doc | Path |
|-----|------|
| Research | `.research/` (Wolverine cascade/tx ground-truth + correct-CQRS-dispatch findings, 2026-06 branch session) |
| API decisions | `.research/m2-api-surface-decisions.md` |

---

### M2b — Durability [DONE] (depends on M2a)

Inbox + partitioning. Can ship in two sub-steps (M2b.1 inbox → M2b.2 partitioning).

#### Durable Inbox

Write-ahead persistence for idempotent consumption.

- [x] Inbox store (`IInboxStore` protocol + SQLAlchemy)
- [x] Inbox table (`inbox_entries`) with composite-PK dedup on `(message_id, destination=handler-FQN)` (per-handler, not just `message_id`)
- [x] Inbound external-transport consumption — **DONE in-process (`a29a744`)** via the Inbox Listener milestone + **Transport Collection T1+F1** (bidirectional `FastStreamRabbitTransport`, scheme registry, **transport-no-serialize**; memories `decision_transport_no_serialize` / `decision_transport_port_exports`). **Slice C** (separate-process consumer) ✅ **DONE (`c491f8b`)** — consumer-only Waku node + `WakuApplication.run()` graceful shutdown; "ship both" commitment fulfilled; gate green 2762/98.95%. **Slice B** (CB-stop-listener + watermark, gap #2) ✅ **DONE (`23df594`)**. **T2** (Kafka adapter, aiokafka) ✅ **DONE (`e7b3b0e`, verified green 2840/98.93%)** — `group_id`→Kafka key, consumer-group separate, broker-honest disposition. **Transport collection (2 brokers, Wolverine-parity) complete.** See the Inbox Listener milestone below.
- [x] `DurableLocalQueueEndpoint` (inbox-backed local queue)
- [x] Mark-as-handled after successful processing
- [x] Inbox recovery worker (reclaim stale entries + cleanup expired handled)
- [x] Race-window guard via `FOR UPDATE SKIP LOCKED` held across `PROCESSING → HANDLED` (spec §4 `IInboxStore.fetch_pending`)
- [x] `keep_after_handled` default `timedelta(minutes=5)` — Wolverine-aligned dedup window
- [x] Integration with error handling / dead letter

| Doc | Path |
|-----|------|
| Research | `.research/wolverine-inbox-pattern.md` |
| Decision | Memory: `decision_inbox_pattern.md` |

#### Partition-Sequential Processing

Per-aggregate / per-entity ordering for both outbox and inbox.

- [x] `group_id` field on `MessageEnvelope` (rename `stream_id → group_id`, pre-v1.0 breaking)
- [x] Endpoint-level `partition_by=...` strategy
- [x] `ISequenceAllocator` (per-group atomic counter, stukachok pattern) — user-provided infra + startup validation guard
- [x] Head-of-queue fetch — outbox `DISTINCT ON (group_id)`; inbox `DISTINCT ON (group_id, destination)` (composite-key, so fan-out handler rows aren't collapsed); base-table `FOR UPDATE OF … SKIP LOCKED` (the plan's `FOR UPDATE` over `UNION ALL` is PostgreSQL-invalid)
- [x] Per-partition sequential processing in inbox (SQL seam ready; now drained by the Inbox Pull-Listener below [DONE])
- [x] `group_id` propagation to cascading messages
- [x] Keyless = parallel/unordered (Decision B): no global FIFO lane, no synthetic group_id, no strict-key flag; no `xid8`/`xmin` (row-lock + MVCC suffice)

| Doc | Path |
|-----|------|
| Stukachok ADR | External: `stukachok/docs/adr/001-transactional-outbox-with-per-aggregate-ordering.md` |

#### Inbox Pull-Listener / Crash Recovery [DONE] — correctness item, not a feature

Durable-inbox rows persist correctly (incl. `group_id`/`sequence_number`) and `fetch_pending_partitioned` (head-of-queue, M2b.2) is the ready read seam — but **nothing drains it yet**. The durable local queue processes via the in-memory fast path with the inbox as durable backup; `InboxRecoveryWorker` only reclaims stale *claimed* rows + cleans handled ones. So after a crash, never-claimed `INCOMING` rows are not re-processed. Closing this needs an executor-bearing puller.

**Design (locked):** Wolverine own-on-receive. `DurableLocalQueueEndpoint.dispatch` claims its inbox rows (`owner_id = node`) in the existing INSERT (free) instead of leaving them `NULL` — so every durable persist path is owned, `recover_stale` uniformly handles local-queue crashes too (today it can't — it skips `owner_id IS NULL` rows), and the only `owner_id IS NULL` INCOMING rows are `recover_stale`-released = genuinely abandoned. The drain then reuses `fetch_pending_partitioned` unchanged (no age gate, no new store method). Consolidated into one per-pod inbox durability worker (recover_stale → drain+execute → cleanup_handled) — the Wolverine `DurabilityAgent` shape.

- [x] Pull-and-replay consumer over `fetch_pending_partitioned` (`InboxDrainer` — executor + handler FQN→type resolution + finalize), claim-at-dispatch, consolidated `InboxRecoveryWorker` (recover→drain→cleanup); bounded poison handling via `max_drain_attempts`

**Deferred (explicitly):**
- [ ] **Node-heartbeat liveness** — Waku detects abandonment by a TIME threshold (`stale_threshold` on `updated_at`), NOT Wolverine's node-registry/heartbeat table. **Caveat:** a live handler running longer than `stale_threshold` can be double-run by the drain (at-least-once; idempotency — required of all inbox handlers — covers it). True Wolverine parity (recover only *dead-node*-owned rows) needs a heartbeat table — deferred as a larger feature.
- [x] **Inbox Listener milestone** ✅ **DONE — in-process (`a29a744`):** `IInboundTransport` port + FastStream-in-process RabbitMQ adapter + `DurableInboxReceiver` (persist→ack→handoff) + `inbox/listener.py`, wired via `MessagingConfig.inbound` (requires `inbox` + ≥1 listener). The real inbound external path; replaced the removed `DurableReceiver` stub. **Separate-process variant ✅ DONE (Slice C, `c491f8b`)** — consumer-only Waku node, full resilience; FastStream-driven "degrade" variant DROPPED (see the Slice C UPDATE below). Verified viable on **FastStream 0.7.1** (embeddable `RabbitBroker`, `AckPolicy.MANUAL`, per-subscriber pause + `Channel(prefetch_count)`, free `connect_robust` reconnection). Form = **persist → ack → hand off** to the existing durable worker/drainer (NOT inline like the old stub). Two deploy variants, **ship both together** (decided 2026-06-21, avoid revisiting the feature; forces a clean port boundary) — in-process (default; full Waku-owned CB-pause + watermark backpressure) and separate-process (valid; those degrade to broker prefetch + drainer; adds a public consumer entrypoint + container bootstrap + example). Prereqs both cleared (2026-06-21): **F-END-1** ✅ (shared `MemoryStreamWorker[_ItemT]`, durable composes it) + **Slice 2** ✅ (gap #18 delivery options — reserves the `ack` field) — milestone now fully unblocked. Target architecture: `.research/2026-06-21-inbound-listener-target-architecture.md`; decision: memory `decision_inbound_listener`. Supersedes the old "wire DurableReceiver into FastStream" + the publish-only stance (`project_faststream_constraint` updated). Also unblocks Backpressure (gap #2) below.
  - **UPDATE 2026-06-24 — Slice C ✅ DONE (`c491f8b`, gate green 2762/98.95%):** the separate-process variant shipped as a full-resilience **consumer-only Waku node** (Wolverine-faithful: full runtime, owns the embedded broker, pure config — `transports`+`inbound`+`inbox`, no `outbox`/HTTP, NOT a mode). The earlier "separate-process degrades to broker prefetch + drainer" framing and the **FastStream-driven external-consumer variant are DROPPED** (no Wolverine analog; that was the only source of the resilience asymmetry — now removed). New code only: `WakuApplication.run()`+`request_shutdown()` (Approach A) on a narrow shared `wait_for_shutdown` util (DRY with the projection runner) + `resolve_default_scheme` dedup + example/docs. No Solo/Balanced dial (SKIP-LOCKED recovery is concurrency-safe by construction); shutdown drain bound stays at the transport, not `run()`. Spec/plan: `.research/2026-06-24-slice-c-separate-process-consumer-{design,plan}.md`; decision: `decision_slice_c_separate_process_consumer`.

#### Scheduled Messages — DONE (durable-local, via gap #18)

- [x] `execution_time` column on **inbox** entries + the `ScheduledPromotionWorker` promotion poll (`inbox/scheduled.py`)
- [ ] `execution_time` column on **outbox** entries — not built (not needed for the durable-local `schedule_send` path)
- [x] Polling respects `execution_time <= NOW()` (inbox promotion, dedicated `scheduled_poll_interval` + jitter)
- [x] Full bus API (`schedule_send(message, at/delay)`) — shipped with DeliveryOptions (gap #18)

---

### M2c — Production Ops [DLQ DONE · OTel ACCEPTED (post-fix-program) · Backpressure DONE (Slice B)] (parallel to M2b after M2a)

New work surfaced by the Wolverine gap analysis. Three independent subsystems.

#### OpenTelemetry Instrumentation (gap #5) — ACCEPTED, sequenced post-fix-program

**ACCEPTED direction, COMMITTED to implement** (dex, 2026-07-13) — no longer parked. Sequenced AFTER the pre-1.0 fix
program (slices #24–#28); still needs a full spec pass (span boundaries + context propagation deserve M2d-level rigor)
before an implementation slice. Design in memory `project_otel_optin_design` (rewritten 2026-07-13 — the original
bespoke-tracing-behavior sketch is superseded by the #20 observer seam).

Shape: the tracer is a **conditional `IMessageObserver`** contributed via `MessagingConfig.observers` (the `b582a5a`
observer seam — ZERO-call when nothing is registered — REPLACES the old bespoke `IPipelineBehavior` mechanism); a
`waku[otel]` extra + an opt-in `OpenTelemetryModule.register()` keep `opentelemetry` out of non-OTel lockfiles and out
of core; W3C `traceparent` rides in `MessageEnvelope.headers` to stitch traces across the outbox→transport→inbox
process hop; `ITelemetry` is narrowed to a thin span-object abstraction (keep-vs-drop decided at spec time).

- [ ] `waku[otel]` extra + opt-in `OpenTelemetryModule.register()` (no dependency / runtime / layering cost when off)
- [ ] Tracing `IMessageObserver` via `MessagingConfig.observers` (spans for send / pipeline behaviors / handle / transport send / inbox receive)
- [ ] Trace context propagation via envelope `headers` (W3C `traceparent`; decision already made)
- [ ] Metrics: messages sent / received / failed / dead-lettered, per message type
- [ ] Per-message-type trace sampling config (silence noisy types)
- [ ] Outbox relay + inbox recovery worker spans

#### Dead Letter Queue Management (gap #4) — DONE (M2c)

**FOUNDATION** (dlq-replay-foundation slice):
- [x] Dead-letter entry gains `status` + `replay_count` columns (NO `headers` column — `payload` already stores the full serialized envelope incl. headers)
- [x] `IDeadLetterStore` gains `mark_replayed` / `mark_replay_failed` (and the foundation's `fetch_replayable`, since removed in M2c — superseded by `claim_replayable` + `query`)
- [x] Document the replay CONTRACT (read entry → deserialize `payload` → re-inject to `destination` → mark outcome; at-least-once, leans on inbox dedup)

**M2c — the replay machinery itself:**
- [x] `IDeadLetterStore.query(DeadLetterQuery)` — list/filter DLQ entries (status/type/destination/time, newest-first, paginated; Always-Valid `limit`/`offset`)
- [x] Replay EXECUTOR — `ReplayExecutor.replay(entry)` / `replay_by_id(entry_id)`: deserialize → `MessageRouter.endpoint_for(destination)` → re-dispatch preserving `message_id` → mark outcome; never commits
- [x] `IDeadLetterStore.claim_replayable(batch_size, max_replay_count)` — poller claim, `FOR UPDATE SKIP LOCKED`, PENDING + REPLAY_FAILED-under-limit
- [x] Auto-replay poller — `DeadLetterWorker` (1-per-DC, opt-in `auto_replay_enabled`, AdaptiveInterval, claim-and-hold)
- [x] Periodic DLQ cleanup worker — same `DeadLetterWorker`, opt-in `retention` (None = off; Wolverine's documented default is 30d), drives existing `purge(older_than)`
- [x] `DeadLetterConfig` (store + `auto_replay_enabled` / `max_replay_count` / `retention` / `cleanup_interval` / poll tuning); `MessagingConfig.dead_letter_store` → `dead_letter`
- [ ] CLI / REST replay surface — **still deferred** (presentation-layer; the programmatic `ReplayExecutor` + `query` are the operator seam)

#### Backpressure / Watermarks (gap #2) — ✅ DONE 2026-06-24 (Slice B)

Wolverine's model (pause the broker LISTENER on high watermark so the broker holds the backlog)
needs a Waku-owned, **pausable listener** feeding the queue. That listener now EXISTS — the in-process
`RabbitBroker` Inbox Listener landed (`a29a744`) — so this is no longer blocked on a missing listener; the
remaining work is the watermark/pause-on-depth logic itself. Local queues (BUFFERED/DURABLE)
are fed by in-process `dispatch()`, where the existing bounded `max_buffer_size` already applies
producer backpressure (full → `send()` blocks). The existing `pause()`/`resume()` pauses *processing*
(the wrong direction for queue-depth backpressure — but exactly what the Circuit Breaker consumes).

**✅ DONE (Slice B, 2026-06-24)** — built faithful to Wolverine SOURCE: CB and watermark BOTH stop the broker
LISTENER (one refcounted `ListenerBackpressure` gate; `Subscription.pause/resume` → FastStream `RabbitSubscriber.stop/start`);
CB timed-resume, watermark depth-resume; processing never paused for inbound; M2d local-queue CB unchanged. Verified green
2802/98.93% (working tree, pending commit). Spec/plan: `.research/2026-06-24-slice-b-listener-backpressure-{design,plan}.md`;
decision: `decision_slice_b_listener_backpressure`.

- [x] `BufferingLimits(high, low)` config + `ListenerBackpressure` (one refcounted listener gate) — Slice B
- [x] Queue-depth monitoring (`MemoryStreamWorker.queue_depth` + `on_drain`) driving listener stop/resume — Slice B; the CB ALSO stops the listener (faithful: CB→listener, not processing), shared resume via the gate refcount
- [x] Pause upstream listener on `high_watermark`, resume on `low_watermark` — ✅ Slice B (`ListenerBackpressure` over the Waku-owned listener `Subscription`; `23df594`)
- [ ] Applies to BUFFERED and DURABLE modes

---

### M2d — Sending Resilience [DONE] (parallel to M2b/c after M2a)

#### Sending Failure Policies (gap #3) — DONE

Mirror `ErrorPolicy` for outbound `ITransport.send()` failures. Built on a neutral escalation
kernel (`messaging/_escalation.py`) extracted from `errors/` (pure move; `errors` + `sending` are
now siblings over the kernel). Design: `docs/plans/2026-06-16-m2d-sending-failure-policies-design.md`.

- [x] `SendingFailurePolicy` builder (exception → action) — distinct type, mirrors `ErrorPolicy`'s shape
- [x] Per-endpoint `ExternalEntry.sending_failure_policies` + global `MessagingConfig.default_sending_failure_policies`; `SendingFailurePolicyRegistry` keyed by destination URI
- [x] Actions: retry, retry-with-backoff, discard (→ new `OutboxStatus.DISCARDED` + `IOutboxStore.mark_discarded`), move-to-DLQ
- [x] Applied by the outbox relay — SINGLE retry authority: `OutboxRelayConfig` retry tuning expressed as a synthesized catch-all policy (`build_relay_default_policy`), so `_on_dispatch_failure` is one `evaluate → apply` path (behavior-equivalent to the old fixed loop)
- [x] Documented interaction with handler `ErrorPolicy` (DISJOINT domains: inbound handler failures vs outbound transport-send failures)
- [x] Divergence (documented): `SendingFailurePolicy` REQUIRES an explicit terminal (enforced at registry build) — a durable outbox message must not be silently dropped on exhaustion; `ErrorPolicy` keeps the implicit-discard
- [ ] **pause-sending** action — DEFERRED to the **M3 sending-side circuit breaker (gap #15)**. The shipped M2d Circuit Breaker is inbound-PROCESSING only (G3); sending-pause was NOT folded in and depends on a future send-side CB. Reserved (no enum member/builder)
- [ ] Direct (non-outbox) transport-send path — does not exist today; the registry/evaluator are reusable by it later, not wired now (YAGNI)

#### Circuit Breaker (state machine) — DONE

Closes the M2 roadmap (excl. OTel, accepted post-fix-program). Per-endpoint, **rate-based** processing circuit breaker for
INBOUND message handling on BUFFERED + DURABLE local-queue endpoints. Consumes the existing
`pause()`/`resume()` (M2a, processing-pause) — a trip pauses the endpoint's worker so it stops hammering a
broken downstream, then resumes after a cooldown. Wolverine-faithful: CLOSED → OPEN(pause) → resume+reset
(**NO half-open probe**). New `messaging/circuit_breaker/` (`CircuitBreakerConfig` + `CircuitBreaker` state
machine with an injectable clock/sleep for deterministic tests); `EndpointExecutor.execute(on_result=...)`
observer feeds each message's terminal `ExecutionOutcome`; the endpoint owns its CB and pauses/resumes
itself. NOT blocked by Backpressure/gap #2 (that needs feeder-pause; the CB needs processing-pause, which
already exists). Design/plan: `docs/plans/2026-06-16-m2d-circuit-breaker-{design,plan}.md`; decision memory
`decision_m2d_circuit_breaker.md`.

- [x] Circuit breaker state machine — CLOSED → OPEN(pause) → resume+reset (Wolverine model; **no half-open** — G1)
- [x] Configurable thresholds — `failure_rate_threshold` (fraction 0.0–1.0, **not** int-percent — G5), `tracking_period` (rolling window), `minimum_throughput`, `pause_time`
- [x] Per-endpoint, rate-based — ONE CB per endpoint; `track_exceptions`/`ignore_exceptions` decide which failures *count* (the correct reading of "per-exception-type" — NOT a separate circuit per exception)
- [x] `DurableLocalQueueEndpoint.pause()/resume()` implemented (was a no-op) — `_paused` gate in the worker loop (G2)
- [x] `on_result` observer on `EndpointExecutor` — fired once per handler-execution, by terminal outcome; `DISCARDED` not counted (policy-intentional drop — G7)
- [ ] Sending-failure circuit breaker deferred to M3 (gap #15; depends on this + Sending Failure Policies — G4)

**Documented gaps / divergences (carried forward):**
- **G1** No classic half-open trial-probe — Waku CB = CLOSED → OPEN(pause) → resume+resample (matches *real* Wolverine; diverges from this roadmap's earlier "half-open" wording).
- **G2** `DurableLocalQueueEndpoint.pause()/resume()` newly implemented (previously the no-op `Endpoint` ABC default) — `_paused` `asyncio.Event` gate; `stop()` sets it first to avoid a shutdown-while-tripped force-cancel.
- **G3** INLINE / EXTERNAL endpoints out of scope (no processing loop / send-side respectively).
- **G4** Sending-side circuit breaker (gap #15) deferred to M3 — depends on this + Sending Failure Policies.
- **G5** `failure_rate_threshold` is a **fraction (0.0–1.0)**, not Wolverine's int-percent — Pythonic clarity.
- **G6** **Per-endpoint only, no global default** — matches Wolverine (CB is endpoint-specific); intentional divergence from the error/sending "global default + per-endpoint" pattern.
- **G7** `ExecutionOutcome.DISCARDED` is **not counted** (policy-intentional drop, not a downstream failure).

### M2e — ES ↔ Messaging Event Forwarding [DONE] (depends on M2a.4 + M2b.1 + M2a.5)

Makes the Critter-Stack "command → aggregate decides → events appended + forwarded to messaging, atomically" flow first-class. ES command handlers are already messaging `RequestHandler`s — they ride the pipeline (transaction, error policies, cascade) by construction — but the ES↔messaging *event* seam is pre-M2 and currently broken. Verified against Marten+Wolverine (see `.research/m2-plan-update-tracker.md` "ES ↔ MESSAGING INTEGRATION REVIEW" + memory `decision_m2e_forwarding_wiring.md`). Design plan: `docs/plans/2026-04-13-m2e-es-messaging-event-forwarding.md`.

- [x] **A3 — Event-Forwarding as a pipeline BEHAVIOR.** Marten/Wolverine forward at session/store level, but Waku's ES store is too low a layer to know the bus/router. A new auto-registered `EventForwardingBehavior` runs in the ES handler's scope, **inner to `OutboxCascadingBehavior`**: the store records appended events into a scoped collector (`IAppendedEvents`); the behavior PRODUCES them into `IOutgoingMessages`; `OutboxCascadingBehavior` is the sole CONSUMER that drains the bucket to the outbox in-tx. One drain, **no double-flush**. Replaces the torn-write in-handler `publish()` loops (`eventsourcing/handler.py:67-68`, `eventsourcing/decider/handler.py:75-76`) that bypass the outbox pre-commit. = Marten **Event Forwarding** parity.
- [x] **B — subscriber-gated, raw-by-default + opt-in translation seam.** Forward an appended event ONLY if it has a registered route (reuse `MessageRouter.resolve`; = `publish` no-op-if-no-subscriber). Forward raw `IEvent` as-is by default (Waku has one `IEvent` marker — no domain/integration split). Opt-in `forward(EventType).transformed_to(...)` (Marten `SubscribeToEvent<T>().TransformedTo(...)` analog) maps internal→integration before forwarding. Raw is the default; no mandated integration-event type.
- [x] **C (strengthened) — startup session-IDENTITY validation + opt-in helper.** `_UnitOfWorkValidationExtension` (`messaging/modules.py:324-335`) checks only UoW PRESENCE today, so append+outbox atomicity is not actually guaranteed. Strengthen it to verify session-object identity (event-store session `is` UoW session, + outbox), raising `ImproperlyConfiguredError` on mismatch. Plus an OPT-IN `scoped(AsyncSession)` paved-road helper (Waku `Enroll(session)` analog) — NOT mandated (Waku is BYO-session). Prerequisite for A's atomicity; can land first.
- [ ] **D — DEFER (sugar only).** `[AggregateHandler]`-style decorator sugar + multi-stream/DCB are deferred (Waku is ~70% there via `EventSourcedCommandHandler`/`DeciderCommandHandler` load→decide→save). **Deferring D does NOT defer forwarding** — forwarding is A and ships in M2e.

Same-tx domain events are a LIGHT opt-in routed via `invoke(event)` (M2a.5) instead of the outbox; default = post-commit outbox forward. No heavy domain-vs-integration type framework. Foundation is correct — **no messaging-API redesign**; these are ES-side additions onto the M2 primitives.

---

## M2→M3 Consolidation Gate [COMPLETE]

Pre-v1.0 first-principles consolidation from the M2 architecture review. Re-derives accreted structural
decisions toward the clean end-state (breaking changes OK but each NAMED). Preserves ALL correctness
invariants (stores-never-commit, FOR UPDATE SKIP LOCKED, composite dedup, persist-before-enqueue,
1-per-DC, forwarding retry-safety, shared-session atomicity, `_TransactionDepth`).

| Doc | Path |
|-----|------|
| Architecture review (findings) | `.research/2026-06-16-messaging-es-architecture-review.md` |
| Target design (C1–C11, F1–F4) | `.research/2026-06-17-messaging-es-target-design.md` |
| Decision: behavior-policy model | Memory: `decision_behavior_policy_model.md` |
| Decision: TST-1 store-factory | Memory: `decision_tst1_store_factory_lighter.md` |

**Dependency-ordered slices** (each = plan → execute (fresh session) → review cycle; detailed plans are
LOCAL-ONLY in `.research/`). **Order: C10 → F1 → F2 → {C5, C6, F3/F4, C8, C9/C11 — independent, batchable}.**

| Slice | Scope | Status | Plan |
|-------|-------|--------|------|
| **0 — Correctness** | TXN-1 (partition-head FIFO), ERR-2 (durable DLQ data loss), LAY-1 (finalize unify), TST-1 (SKIP-LOCKED contract suite) | ✅ done — green (2416 tests, 98.82%), 3-agent review + independent verify PASS | `.research/2026-06-17-slice0-plan.md` |
| **C10 — Behavior-policy + ext-API** | `IBehaviorPolicy`/`Position` (replaces MediatR-blanket assembly), `RegistryAggregator` base, `OnContainerBuilt` phase, `unit_of_work_scope`; deletes `additional_behaviors` smuggle + `check_forwarding_preserved` | ✅ done — green, 3-agent review + independent verify PASS (F1 now unblocked) | `.research/2026-06-17-c10-plan.md` |
| **F1 — ES↔messaging decouple** | neutral `waku.messages` (`IMessage`/`IEvent`/`MessageIdentity`), ES core standalone (no messaging import), command handlers + forwarding + session-check → `waku.integrations.eventsourcing_messaging` | ✅ done + committed — ES core zero-messaging-import enforced by import-linter contract, verified | `.research/2026-06-17-f1-plan.md` |
| **F2 — Serialization unify** | shared `PayloadCodec` + `MessageIdentity` + one upcaster (upcast-on-inbound); two registry facades; **Option C** versioning — first-class `MessageEnvelope.message_version`, bare `message_type` (restores `decision_message_type_naming`'s recorded field; undoes `name.vN` drift) | ✅ done — green (2470 tests, 98.85%), lint/typecheck clean, 3-agent review PASS; + single-home upcasting cleanup (`waku.serialization`, `IPayloadUpcaster`, ES `upcasting` package + `IEventUpcaster` dropped). **Substrate only — messaging-upcast WIRING deferred to M3** (see M3 note) | `.research/2026-06-18-f2-plan.md` |
| **C5 — Escalation unify** | one `Self`-typed `EscalationChain` base; keep 2 public types (`policies_need_dead_letter` + `RetryAction`/`RetryStage` re-exports preserved in `errors/`) | ✅ done + committed (`8210857`), verified | `.research/2026-06-18-c5c6-plan.md` |
| **C6 — DurabilityAgent base** | `_PollingAgent` + `IPaceStrategy` + `Placement`; M3 heartbeat seat; **also fixes a latent relay crash** (current `_run_loop` lacks loop-level try/except) | ✅ done + committed (`8210857`), verified | `.research/2026-06-18-c5c6-plan.md` |
| **F3/F4 — API surface** | keep 2-axes bus (Wolverine-validated) + friendlier `NoRouteError`; config renames (`InboxConfig`: `drain_batch_size→batch_size`, `stale_threshold→stuck_threshold`); shared `PollingConfig` in **`waku._internal.polling`** (re-exported both sides — C6-reusable, no ES→messaging back-edge); resiliency/port exports; `.bind(Handler)` inference; relax vestigial durable-tx validation | ✅ done — verified (2460 tests, 98.87%, import-linter 3/3); review wired the dead `default_circuit_breaker` field + behavioral test; neuroslop test purge (`feedback_no_import_shape_tests`) | `.research/2026-06-18-f3f4-plan.md` |
| **C8 — Docs + ledger + hygiene** | `retry_policy_builder` REVERSE, error-handling docs rewrite to shipped API, ERR-4 (drop prod `assert`)/API-3 (`ErrorPolicy`→`ProjectionErrorPolicy`), ISP `exists` drop (LAY-5 already folded into F1) | ✅ done — green, diff audit + `task all` PASS | `.research/2026-06-19-final-batch-plan.md` |
| **C9/C11 — Test-fidelity + internal** | TST-2/3 (sleep→observable-effect waits, executor sleep seam), INT-1 (executor reads `envelope.message_type`, drops parasitic registry dep), INT-2 (destination/uri/group_id `NewType` identifiers) | ✅ done — green, diff audit + `task all` PASS (TST-1 done in Slice 0) | `.research/2026-06-19-final-batch-plan.md` |

**Deferred from this gate (named):** the ES↔messaging coupling redesign (F1) marks `decision_m2e_forwarding_wiring`
Option A as SUPERSEDED; `transaction-architecture-analysis.md` to be marked SUPERSEDED on Slice 0 commit;
`decision_retry_policy_builder` REVERSE (taught a non-existent API) — DONE in C8 (memory corrected). M3 merges: Message Versioning ⇄ F2
upcaster; `waku_nodes` heartbeat ⇄ C6 `_PollingAgent` seat; Storage Admin CLI ⇄ DLQ replay surface.

---

## M3 — Developer Experience + Ops Completeness [IN PROGRESS]

### Documentation IA Restructure [DONE 2026-07-12]

The docs are re-cut on **Candidate B — concern-lifecycle** (Start / Foundation / Messages & Handlers / Messaging
Runtime & Transports / Event Sourcing / Reference / Project). 49 user-facing pages, each in the nav exactly once; zero
file moves and zero URL churn (zensical nav grouping is path-independent). Starting point was an M1b-era tree with
35/73 capabilities documented (48%).

- [x] docs-accuracy slice (`b1ed4cc`) — five pages brought to current-code truth (codec model, config table, projection policies)
- [x] docs-completeness slice (`4c3177a`) — the seven flagship features documented, both journeys made runnable
- [x] Candidate-B nav re-cut (`21aaff8`) — 10 new pages incl. `features/messaging/runtime.md` (operational semantics:
  delivery guarantees, at-least-once + idempotency contract, 3-tx model, per-group ordering, backpressure),
  `features/messaging/{contracts,handlers,inbox,observability}.md`, `features/messaging/transports/{rabbitmq,kafka}.md`,
  `reference/configuration.md`, `features/eventsourcing/concepts.md`, `mediator.md`; 2 orphan indexes deleted
- [ ] Tutorials/journeys layer (post-re-cut, Wolverine-style) — future
- [ ] Follow-ups the docs slices deliberately left: stale `MessageRegistry` example in
  `advanced/extensions/custom-extensions.md` (✅ done in #22 — rewritten against `HandlerMap`/`HandlerMapAggregator`,
  blessed imports only); `outbox.md` bare `OutboxConfig(store=...)` DX defect (✅ resolved by #21 — `AsyncSession`
  imports made runtime, `.store` config fields deleted outright, `outbox.md` rewired to the backend import);
  `examples/messaging/consumer.py` `build_inbox_store()` handwave (✅ resolved by #21 — real
  `SqlAlchemyBackend.register` wiring); still open: homepage orientation trim (dex to art-direct);
  `contributing/docs.md` broken include-anchor

### Message Versioning & Forwarding

Schema evolution for persistent + wire messages. **Two complementary mechanisms** sharing the naming/version
chain: (a) dict→dict **upcasting** *before* deserialization (same type, schema evolution); (b) typed **forwarding**
`IForwardsTo[T]` *after* deserialization (old type → new type).

**F2 already shipped the upcast SUBSTRATE** — on-wire `MessageEnvelope.message_version`, producer stamping via
`MessageIdentity.version`, `JsonEnvelopeSerializer.deserialize` → `codec.decode((message_type, message_version))`,
shared `UpcasterChain` engine in `waku.serialization`, and an empty messaging chain in `_create_envelope_codec`
ready to populate. What remains is the WIRING, deferred here deliberately: designing the registration surface in
isolation from `IForwardsTo[T]` + forward-naming (they share the version chain), with no current consumer
(`speculation_needs_signal`), and before **F3/F4** settles the `MessagingConfig` surface, would be a crutch. F2's
decode runs *before* deserialization and `IForwardsTo[T]` runs *after* — complementary layers, so F2 baked in no
blocker.

- [ ] `IForwardsTo[T]` protocol (old version → new version transform) — mechanism (b)
- [ ] Forward naming in naming chain
- [ ] Bridge with ES upcasting infrastructure — mechanism (a) wiring on top of the F2 substrate: messaging-upcaster
  registration surface (mirror ES `EventType(upcasters=...)`), chain assembly + DI into `_create_envelope_codec`
  (mirror `_build_message_type_registry`), and move `_validate_upcaster_versions` (`from_version < version`) into
  `waku.serialization` once messaging is the real second consumer

### Side Effects / Deferred Operations

Handler-produced operations that execute inline (same transaction), unlike cascading messages.

- [ ] Evaluate need — may be covered by explicit handler code + pipeline behaviors

### Conventional Routing (gap #10)

Auto-map message types to endpoints by convention. Reduces `route()` boilerplate.

- [ ] `RoutingConvention` protocol (message type → endpoint URI)
- [ ] Namespace-based convention (`module.Command` → queue by module)
- [ ] Interoperates with explicit `route()` calls (explicit wins)

### Scheduled Messages — Bus API ✅ DONE (gap #18)

- [x] `schedule_send(message, delay)` / `schedule_send(message, at)` on `IMessageBus` — shipped with DeliveryOptions
- [x] Backed by `execution_time` column (inbox) — durable-local path; promotion at `scheduled_poll_interval`

### Inferred Message Grouping

Wolverine parity: auto-detect `group_id` from saga id / aggregate id / sequenced message order. Removes explicit `partition_by` for common cases.

- [ ] `UseInferredMessageGrouping()` config option
- [ ] Conventions for saga/aggregate/sequenced detection

### Storage Admin API (gap #14)

Programmatic + CLI tooling for ops and tests.

- [ ] `IMessageStore.admin.rebuild()` / `.clear()` for outbox, inbox, DLQ
- [ ] CLI commands (`waku storage rebuild/clear/replay`)

### Sending Failure Circuit Breaker (gap #15)

Extends M2d sending failure policies with circuit breaker semantics. Prevents thundering herd on broker recovery.

- [ ] Per-endpoint sending circuit breaker state machine
- [ ] Reference notes (2026-07-08 upstream audit): read the M8 spec's dual-CB table FIRST; mirror Wolverine's
  CircuitWatcher cancel-on-dispose fix (6.17: linked CancellationTokenSource cancelled on dispose, watcher disposed
  before the sender) — the resume-probe loop must die with its owner

### Batch Message Processing (gap #11)

- [ ] Batch handler discovery (`list[T]` / `Sequence[T]`)
- [ ] Configurable batch window (count + time)
- [ ] Tenant-aware batching

### Resource Auto-Provisioning (gap #12)

- [ ] `auto_provision` option on transport config
- [ ] Create queues / topics / tables at startup
- [ ] `UseResourceSetupOnStartup` helper

### Operational Node Visibility (partial gap #6)

Stop-gap for full DurabilityAgent in M4+. Enables "which pod ran the sweep" diagnostics.

- [ ] `waku_nodes` table with heartbeat + pod metadata
- [ ] No leader election yet (still `FOR UPDATE SKIP LOCKED` for concurrency)
- [ ] Stale node eviction

### Pipeline Pre-composition + Introspection (perf, Wolverine HandlerChain analog)

Move per-handler pipeline composition from per-dispatch to startup. Python-faithful
analog of Wolverine's HandlerChain codegen — NOT source generation. Apply only if
profiling shows the per-dispatch behavior-chain walk is hot. Depends on M2a.2
handler-keying (stable per-`(message, handler)` chain → cacheable).

- [ ] Cache the resolution **plan** (ordered behavior types per handler) at startup; stop rebuilding the behavior map + sort per dispatch
- [ ] NEVER cache instances — handler/behavior instances + scoped deps (UoW, repos) stay DI-resolved per request scope (same as Wolverine codegen, which compiles resolution calls, not instances)
- [ ] `describe_pipeline(handler)` introspection API — list wrapping behaviors + order for diagnostics (Wolverine "preview generated code" analog)
- [ ] Gate on a benchmark: define throughput target + measure chain-walk cost before implementing

### Internal Quality Backlog (post-consolidation holistic review)

Source of truth (LOCAL-ONLY, read BEFORE touching any item — full evidence, refutation verdicts, leave-alone
list): **`.research/2026-06-19-holistic-quality-review.md`** (coverage-complete 16-cluster audit, 0 Critical /
4 Important / 23 Minor / 103 leave-alone) and the narrower first pass `.research/2026-06-19-post-gate-quality-review.md`.
Structural health was strong (clean acyclic instability gradient, 0 cycles, all import-linter contracts KEPT) — these
are operational/discoverability/maintainability debt, NOT correctness defects. Each item below cites the report's
finding ID + `file:line`; the report has the why + suggested fix shape.

**Already fixed (do NOT redo)** — landed alongside this backlog, green at 2490 tests / 98.93%: `contracts/__init__.py`
dead barrel removed; `get_handler_types` returns a `tuple` copy (`handler_map.py`); `Endpoint.dispatch(scope)`
semantics documented on the ABC (`endpoints/base.py`); **F-INT-1** `CorrelationEnricher` now degrades via
`try_get_message_context()` instead of hard-failing non-bus appends (`correlation_enricher.py`); **F-OUT-1** outbox
`cleanup_dispatched` now wired into `OutboxRelay._maybe_cleanup` (retention-gated, mirrors `DeadLetterWorker`);
**`unit_of_work_scope` relocated** from `waku.di` to `waku/_internal/transaction.py` (the non-public generic home, next
to `_internal/polling.py`) — removed the `waku.di → waku.uow` layering inversion and the public `waku.di` export
(5 importers + test repointed; import-linter 3/3 KEPT).

**Headline — `DurableLocalQueueEndpoint` is the structural weight center (3 findings converge):**
- [x] **F-END-1** (DONE 2026-06-21 — generic `MemoryStreamWorker[_ItemT]`; durable now *composes* it) — it FORKED the stream/task/drain lifecycle `MemoryStreamWorker`
  already owns: `endpoints/durable_local_queue.py:103-218` vs `endpoints/worker.py:28-152`. Fix: generalize
  `MemoryStreamWorker[T]` so the durable endpoint *composes* it (as `LocalQueueEndpoint` does) instead of
  re-implementing `_send_stream`/`_receive_stream`/`_worker_task`/`start`/`stop`/pause/resume inline.
- [x] Divergent, non-interchangeable `_drain_worker` shutdown strategies across the sibling endpoints — folded:
  durable's `anyio.fail_after` variant dropped; `worker.py`'s `asyncio.wait` version is the single keeper.
- [x] `InboxEntry` write-ahead construction duplicated verbatim — resolved: the duplicate copy lived in
  `inbox/receiver.py`, removed 2026-06-21 with the `DurableReceiver` stub; only the durable endpoint's copy remains.

**Single-home / facade convention (mechanical, high-ROI — the dominant violated rule):**
- [x] **F-INT-2** ✅ DONE (2026-06-23) — `forward` + `ForwardDescriptor` now re-exported from `eventsourcing/__init__`
  (the public home; users call `forward(...)` for `EventSourcingConfig.forwarding=`). `ForwardBuilder` left internal by
  design — an intermediate never named by users (`feedback_public_api_exports`).
- [ ] Cross-package re-export shims duplicate the canonical `waku.messages` import path: `contracts/identity.py:1-8`,
  `contracts/event.py:1-5` — point consumers at the single home.

**Transaction-boundary cohesion** (the `unit_of_work_scope` relocation itself is DONE — see "Already fixed"):
- [x] Relay transaction ownership is result-aware rather than mechanically uniform (Slice A, 2026-07-14): batch claim,
  expiry, and reschedule use the neutral transaction substrate; send failure rolls back before policy evaluation;
  delivered-recording and exhausted fallback keep explicit terminal phases because their outcome depends on whether the
  broker send or previous terminal mutation already succeeded. All phases share `waku._internal.transaction` mechanics,
  and failed cleanup prevents retry, fallback, or a normal result.
- [x] Nested inline transactional handling now uses one physical owner with strict rollback-only propagation. A swallowed
  nested failure rolls back and raises root `UnexpectedRollbackError`; cancellation remains cancellation after shielded
  cleanup; deferred non-durable cascades flush only after committed success. Fresh Slice A verification: 654 focused
  tests and `task all` with 4646 passed / 24 skipped / 98.99% coverage; test-engineer, code-reviewer, and code-architect
  approved the final diff with no Critical/Important findings.
- [ ] Duplicated `PollingConfig` default-tuning literal across `OutboxRelayConfig` (`relay.py:45-50`) and
  `DeadLetterConfig` (`config.py:53-58`) — extract one shared default.
- [ ] **Coverage gap:** `waku/di/` was NOT a holistic-review cluster (audited only via its consumers). Audit the
  package internally — carry-over surfaced by the now-resolved `unit_of_work_scope` placement defect.

**Dead / orphan surface ("implemented + contract-tested + unreachable"):**
- [x] `fetch_and_mark_processing` orphaned on `IOutboxStore` after the relay switched to `fetch_head_of_queue`:
  `outbox/interfaces.py:23-24` + `outbox/sqla/store.py:56-74` — ✅ DONE: dead method removed; contract tests migrated to `fetch_head_of_queue` (keyless-fixture equivalent) + 1 subject test dropped.
- [ ] Two policy registries registered as `object_` providers no DI consumer resolves: `messaging/modules.py:439,445`.

**`messaging/modules.py` internal quality (also the god-module split candidate):**
- [ ] `bind()` escape path accepts a non-`IMessage` `message_type` with no validation; overload dispatch leaks raw
  `TypeError`: `modules.py:214,218-224`.
- [ ] Two near-clone `OnContainerBuilt` validation extensions share an un-extracted require-registered spine:
  `modules.py:519-538,554-585`.
- [ ] `HandlerMapAggregator` (ex-`MessageRegistryAggregator`, renamed in #22) couples `_register_behavior_plan` + `_handler_providers` via shared mutable
  `_seen_behaviors` with no regression test: `modules.py:381,477-479,498-500`.
- [ ] **God-module split** (first review `.research/2026-06-16-…` + holistic): `messaging/modules.py` (645 LOC, MI 22.34)
  and `eventsourcing/modules.py` (481 LOC) are the two lowest-MI files — split by responsibility. Packaging-granularity
  cohesion; ties into the parked `_internal`/public packaging redesign (`project_internal_vs_public_packaging`).

**ES decider / command-handler dedup:**
- [ ] Abstract-by-convention `DeciderRepository`/`SnapshotDeciderRepository` are directly instantiable (no ABC mechanism):
  `decider/repository.py:39,124`.
- [ ] `current_state` is dead weight in base `DeciderRepository.save` (LSP-compat leak of a snapshot concern):
  `decider/repository.py:96`; and the snapshot fallback re-replays the full event stream when `current_state` is None:
  `decider/repository.py:183-190`.
- [ ] `aggregate_name` inference in `__init_subclass__` duplicates `EventSourcedRepository`'s identical machinery:
  `decider/repository.py:43-66`.
- [ ] Duplicate boilerplate across the two command-handler bases with no shared intermediate:
  `integrations/eventsourcing_messaging/handler.py:30-36,86-100` + `decider_handler.py:35-41,90-104`.
- [ ] Cross-package consumption of the `eventsourcing._retry` PRIVATE module from both handler bases:
  `integrations/eventsourcing_messaging/handler.py:10` + `decider_handler.py:10` — promote `_retry` to a sanctioned seam.
- [ ] Poll-loop lifecycle + adaptive pacing duplicated between `CatchUpProjectionRunner` and `messaging.PollingAgent`:
  `eventsourcing/projection/runner.py:120-157` vs `messaging/_polling_agent.py:36-61,136-145` — candidate to share the C6 base.

**Small / contract-hardening:**
- [ ] `IPayloadUpcaster.from_version` is a bare mutable public attribute, not a read-only contract:
  `serialization/upcasting/interfaces.py:9-13`.

**Judged SOUND (leave-alone — do NOT "fix"):** 103 non-risk items incl. the C10 policy model, the shared `_internal`
kernel, ES core, the relay's `IntegrityError` substring-match (driver convention, `store/sqlalchemy/store.py:268`), and
`CircuitBreaker.wait_for_resume` as a test seam. See the report's leave-alone section before second-guessing any.

### Critter Parity Blind-Spots (2026-06-20 audit — M3 candidates)

Source of truth: `.research/2026-06-20-critter-parity-review.md` (203 capabilities; ~62% true Critter parity per the 2026-07-13 recompute [was ~65-75% at this audit]; 51
blind-spots; 13 confirmed-sound divergences — do NOT propose closing those). The 6 highest-impact blind-spots —
capabilities Wolverine/Marten ship that NO existing ROADMAP gap tracked — are promoted to numbered gaps below; the
remaining 45 medium/low blind-spots + the divergence ledger live in the audit doc. The supplementary pass on
serialization / interop / endpoint-conventions (the 3 domains the main audit missed) COMPLETED —
`.research/2026-06-20-critter-parity-serialization-supplement.md` (32 capabilities; 10 blind-spots, but **9 of 10 are
architecturally gated** behind the sound outbound-only-transport + dict-not-`byte[]`-codec divergences — i.e.
scope-deferred-by-design, not oversight). Its one clean promotion + the gated/M4 notes are gaps #22-24 below.

- [x] **gap #16 — Handler execution timeout** ✅ DONE (Slice 1a, green — `move_on_after`+`HandlerTimeoutError`→error_policies, per-handler ClassVar + `endpoint_defaults.execution_timeout=60s`; recovery-path drainer fixed). (`DefaultExecutionTimeout` / `[MessageTimeout]`). A runaway/infinite handler
  holds its worker slot forever; Wolverine ships this default-on at 60s, then `TaskCanceledException` flows through error
  policies. Waku has zero per-message deadline (verified: no `timeout`/`fail_after` in `endpoints/executor.py`). Clean
  injection: `anyio.fail_after` around the attempt in `EndpointExecutor.execute` (owns the loop + injectable `sleep`) +
  `MessagingConfig.endpoint_defaults.execution_timeout` + per-handler ClassVar (mirrors `error_policies`). Cooperative — CPU-bound
  sync handlers stay uninterruptible (matches Wolverine).
- [x] **gap #17 — Pause-on-failure listener** ✅ DONE (Slice 1b, green — `RetryAction.PAUSE`+`REQUEUE` deferred-terminal; refcounted pause-token model composes with the CB; `TimedPauser` in `pauser.py`; bounded `max_requeue_attempts`→DLQ poison ceiling [Wolverine `Requeue().AndPauseProcessing`]; `send_nowait` non-blocking re-enqueue). (`Requeue().AndPauseProcessing` / `PauseAllListeners`). Pause the whole
  listener immediately on a specific known-fatal exception. Waku's only pause is the rate-based Circuit Breaker, which must
  accumulate a failure rate first — it cannot react to ONE fatal exception. Distinct from gap #15 (outbound sending CB).
  Add a 5th escalation continuation feeding the existing `pause()`/`resume()` seam (already wired to the CB). `RetryAction`
  already reserves an unimplemented `REQUEUE` (`_escalation.py:28`).
- [x] **gap #18 — DeliveryOptions per-call override** on `send`/`publish`/`invoke` — DONE (variant A whole). Optional
  `options=` carrier on the three verbs + `schedule_send` sugar; envelope-native overrides
  (`headers`/`correlation_id`/`causation_id`/`group_id`) merged at one seam (`MessageBus._create_envelope` +
  `EnvelopeFactory.create`); scheduled delivery on the durable-local path (SCHEDULED row at dispatch, sequence allocated
  at PROMOTION under SKIP LOCKED via a dedicated jittered `scheduled_poll_interval`); expiration via send-drop +
  receive-discard (`ExecutionOutcome.DISCARDED`, covers live worker AND recovery drainer). One injectable clock
  (`waku/_internal/clock.py::Now`/`utc_now`) threaded through factory/bus/executor/durable-endpoint/drainer/recovery.
  Design: `.research/2026-06-21-slice2-delivery-options-design.md`. **Reserved carrier fields (§11, build later,
  non-breaking):** `tenant_id` → M4+ Multi-Tenancy, `ack` → **Inbox Listener milestone** (which names #18 as a dependency
  — #18 reserves the `ack` field for `AckPolicy.MANUAL`), `content_type` → gap #23/#24, `response_type` → M4+
  Request/Reply. Cross-session record: `decision_delivery_options` memory.
  - Parity (additive, 2026-07-04): `schedule_publish(message, *, at/delay)` is the exact 1:1 analog of Wolverine's
    `ScheduleAsync` — sugar over the publish path (source `IMessageBus.cs:29-61` → `PublishAsync`; docs "short hand for"
    `message-bus.md:391-397`), silent no-op on zero subscribers (`MessageBus.cs:280-281`;
    `EmptyMessageRouter.RouteForPublish → []` `:19-22`), fail-loud `SchedulingNotSupportedError` when any subscriber is
    non-durable (existing gap #18 rule). `schedule_send` is a Waku **extension** with no Wolverine analog — a fail-loud
    scheduled command (raises `NoRouteError`; Wolverine's send path throws `IndeterminateRoutesException`,
    `EmptyMessageRouter.cs:14-17`, but has no scheduled-send verb) — the same deliberate intent-split family as
    `invoke(event)` vs Wolverine's single `InvokeAsync`. Both verbs live on the narrow interfaces:
    `ISender.schedule_send`, `IPublisher.schedule_publish`. Cross-ref: `decision_delivery_options`.
- [ ] **gap #19 — Saga message-identity / correlation** (attributes + convention chain + header tracking). The hardest,
  most load-bearing part of saga design, enumerated nowhere — and it **circularly blocks the already-planned M3 Inferred
  Message Grouping** (saga-id detection). Prerequisite for both Inferred Grouping (M3) and Sagas (M4).
- [x] **gap #20 — Structured per-message-type logging** ✅ **DONE 2026-07-03** (14 commits on `773ff96`, NOT pushed — pending
  squash): every sent/executing/executed message logged keyed on message type (`waku.message.<type>`) with ids + audited
  members as structured fields. Baseline observability; distinct from OTel tracing (gap #5). LANDED: `IMessageObserver` ABC +
  `MessageObservers` total-swallow fan-out + `LoggingMessageObserver` (level-by-outcome, nested `audit`) + `Audit()`/
  `AuditedMemberResolver`; `on_executed` fires before the CB `on_result` hook; monotonic durations; wired via Option-D atomic
  re-slice (ctor injection, NO factory). FOLLOW-UP SLICE (Fable-planned, whole-feature-review-driven): endpoint `destination`
  on `on_executing`/`on_executed` (Wolverine parity) + **`bus.invoke()` now observed** (`MessageDispatcher` = invoke boundary;
  `INVOKE_DESTINATION='invoke://inline'` [D2]; invoke-failure reuses `FAILED_NO_POLICY` [D1]) + total-swallow + `HandlerType`
  export + contract docstrings. Reviews: 3-panel whole-branch (test-engineer/code-architect/feature-dev) + Fable whole-feature +
  per-task — ALL SHIP. Full-feature gate 98.91% / 3246 passed. Wolverine-faithful (source-verified: invoke observed like
  `Executor.InvokeInlineAsync`; destination logged like `ExecuteAsync`).
  OBSERVER-REGISTRATION SLICE (Fable-planned δ+W-1, whole-feature-review-driven): observers declared as CLASSES on the
  messaging config — `MessagingConfig.observers` (GLOBAL tier, mirrors the `global_pipeline_behaviors` config-field precedent)
  + per-endpoint `observers=` kwarg on `listen`/`local_queue`/`external_endpoint` (Wolverine `IWireTap` parity; composed via the
  internal `ObserverPlan`, a `BehaviorPlan` analog; entry-level union-dedup merge). Framework DI-registers the types (no raw
  user-side `many()` needed — that remains a low-level escape hatch, stays global). `bus.invoke()` stays GLOBAL-ONLY, structural
  (dispatcher untouched; source-verified: `Runtime/Handlers/Executor.cs` has 0 `WireTap` refs → invoke fires the tracker only).
  Documented divergences: relay wire-send unobserved (pre-existing); per-endpoint observers get the full hook surface vs
  Wolverine's terminal-only wiretap; type-as-key vs `UseWireTap(serviceKey)`. 14 commits (base 6 + invoke/destination 4 +
  registration δ/W-1 4). 4-agent whole-branch final panel (test-engineer/code-architect/feature-dev:code-reviewer/code-explorer
  = no-crutches) ALL SHIP — 0 Critical/0 Important, subsystem verified CRUTCH-FREE; F1–F5 minor cleanups folded (ABC `__slots__`,
  dead set-operand drop, reserved-`invoke://`-scheme validator guard, concurrency-safety docstring, escape-hatch test).
  invoke-failure logs at ERROR by default = **Wolverine-faithful** (local-source-verified `Executor.cs:82` hardcodes
  `LogLevel.Error`; Wolverine has NO failure-log-level knob nor expected-exceptions mechanism — the only lever is the downstream
  logger level, exactly Wolverine's own answer; tune via `waku.message.<type>`).
- [ ] **gap #20 follow-up (PARKED — only on EXPLICIT request; do NOT build speculatively): expected-exceptions / per-exception log
  level.** Downgrade/suppress ERROR logging for domain exceptions used as control flow on the `bus.invoke()` request/response path
  (e.g. `raise EntityNotFound`). Goes BEYOND Wolverine (no reference mechanism to mirror — Wolverine hardcodes failure→Error). A
  scoped standalone feature (e.g. `MessagingConfig.expected_exceptions` → logger observer emits those at WARNING/below) IF dex asks.
  Artifacts (`.research/`, LOCAL-ONLY, dex commits): `2026-06-23-gap20-structured-logging-design.md` (spec — ratified;
  architect + fresh-Wolverine reviewed, folds applied), `2026-06-23-gap20-structured-logging-plan.md` (TDD plan Rev 3 —
  test-engineer + code-architect reviewed, critical findings folded; 6 tasks, no commit steps), `2026-06-23-gap20-exec-kickoff.md`
  (subagent-driven handoff; 6 tasks; 3 mandatory final code reviews; risk list). **Ratified design** (survived both review
  rounds): a generalized `IMessageObserver` ABC seam (logging = observer #1; OTel/wire-tap hang off it later, zero
  hot-loop change), `on_executed` fired before the CB control hook, monotonic durations, `Annotated[T, Audit()]` + a
  config escape hatch, `group_id` as a standard envelope field (auto-audited-member inference deferred to
  saga/stream-id). Five design forks + the audited-member mechanism ratified; every Wolverine divergence documented.
- [ ] **gap #21 — Multi-stream commands in one transaction** (`[WriteAggregate]` params, `VersionSource`) — write several
  ES streams atomically from one handler; plus the related Critter ES read-path blind-spots (`FetchLatest`/`[ReadAggregate]`,
  `MartenOps` side effects) in the audit doc. NB: **MultiStreamProjection + Subscriptions** (Marten's 2nd-most-used
  projection + a core event→bus bridge) are also untracked — only an M4+ titled stub today; promote when M3 ES work lands.

**From the serialization / interop / endpoint-conventions supplement (`.research/2026-06-20-critter-parity-serialization-supplement.md`):**

- [x] **gap #22 — Global durability policy** ✅ DONE (Slice 1a, green — `endpoint_defaults.mode` + `_effective_mode` routing all 3 mode readers; sentinel `MISSING` convention established for `mode`/`circuit_breaker`/`execution_timeout`). (`UseDurableInboxOnAllListeners` / outbox-on-all-sending). Durability was a per-endpoint *construction* choice only
  (`LocalQueueEntry.mode == EndpointMode.DURABLE` + `config.inbox`) with no global override; the global fallback now
  shadows per-endpoint through `MessagingConfig.endpoint_defaults`, honored in `_create_endpoint`. NOT gated on gap #10.
- [x] **gap #23 — Per-transport `IEnvelopeMapper`** ✅ **DONE (`579099e`)** (`MapEnvelopeToOutgoing` / `MapIncomingToEnvelope`).
  Pluggable per-transport mapper + full envelope decomposition (retired `JsonEnvelopeSerializer`): envelope = codec-payload +
  typed `EnvelopeMetadata` everywhere; per-broker Wolverine wire (payload-in-value + metadata-in-headers, user headers bare);
  persistence = payload-blob + `metadata_` JSON + typed columns. Breaking (pre-v1.0). Subsumed selective header↔property
  override. Memory `project_envelope_mapper_parity`; spec `.research/2026-06-25-envelope-mapper-parity-design.md`.
- [x] **Unified bidirectional Endpoint model** ✅ **DONE (`95cdb2b`, squashed, NOT pushed — 2026-07-01)** — the foundational
  refactor sequenced BEFORE #20/#24 so both get a per-URI home by construction. Collapsed Waku's two parallel messaging
  subsystems (inbound `config.inbound`/`InboundEntry` + outbound `config.endpoints`/`ExternalEntry`) into ONE URI-keyed
  bidirectional `Endpoint`: `BrokerEndpointEntry` + direction as `ListenAspect`/`SendAspect` presence (invalid states
  unrepresentable); a single `merge_broker_endpoints` pass is the SOLE producer, with `TransportRegistry`/`RoutingTable`/
  send-failure-registry/listener-wiring as pure projections (none re-derives); the wire mapper has ONE runtime home
  (`registry.mapper_for(uri)`, read by both send + receive); a URI declared on both `external_endpoint`+`listen` merges into
  one endpoint. Fail-loud config validation (conflicting per-URI mapper/`partition_by`, listen-without-inbox, `partition_by`
  on non-DURABLE local, routing/replay to a listen-only endpoint, local↔broker URI collision). Wolverine-faithful (one
  bidirectional Endpoint per URI, mapper bound once); 3-way final review all SHIP (0 Critical/0 Important); gate 3110/98.87%.
  Memory `project_unified_endpoint_model`; spec/plan `.research/2026-07-01-unified-endpoint-model-{design,plan}.md`.
- [ ] **gap #24 — CloudEvents interop** (`id`/`source`/`type`/`specversion`/`traceparent` JSON envelope). Strongest
  format-interop candidate: vendor-neutral CNCF standard, outbound emit-shape fully reachable under the single-codec
  divergence, `traceparent` dovetails with the accepted (post-fix-program) OTel (`project_otel_optin_design`). Built on the gap #23 mapper seam.
- [ ] **Gated / deferred (recorded, not promoted):** MassTransit + NServiceBus interop → **M4+** (inbound half needs an
  in-process receive path Waku deliberately lacks; NServiceBus also needs the M4+ reply-uri substrate). Per-endpoint policy
  convention pass (`ConfigureListeners`) → **M3 sub-item of gap #10** (endpoints are explicitly constructed; payoff scales
  with conventional routing). Multiple binary serializers (MessagePack/Protobuf) + raw-`byte[]` payloads → **M4+** (needs a
  parallel bytes-capable codec port + content-type negotiation; the outbound-only posture does not target it). Custom
  serializer-on-envelope inference → **M3 design-input**, fold into Message Versioning & Forwarding (same `codec.decode` seam).

### Parity Gap Triage (2026-07-04)

Categorization of the **28 untracked** parity gaps from the holistic audit (`.research/2026-07-03-holistic-audit-report.md`
§3.4) into **M3 / M4+ / Declined**. Numbering continues from #24 (highest existing) → gaps #25–#45. Each item:
*name — one-line impact — [source-area]*. Grouped where one design serves several (error-policy builder; exchange/topic
routing) so the shape is settled once. **Totals:** M3 = 21 capabilities (14 entries, #25–#37 + #46); M4+ = 8 (#38–#44 + #47); Declined = 1 (#45).
*(#46/#47 added 2026-07-08 from the upstream delta audit — Wolverine 6.16.0→6.17.0-alpha.1, 98 commits; core mirrored surface verified UNCHANGED, queue unblocked.)*

**Standing decisions folded in here:**
- **Durability Backend Assembly — ✅ DONE (slice #21, 2026-07-12; spec `.research/2026-07-11-durability-backend-assembly-design.md`).**
  Shipped as specced: `waku.backends.{sqlalchemy,memory,testing}` live; six `.store` config fields deleted;
  facet ports single-homed (`waku.messaging.durability`, `waku.eventsourcing.store`); session-identity
  machinery + `shared_session` + store `.session` props deleted; fail-loud `ImproperlyConfiguredError` at
  `OnModuleRegistration` (per-facet provider scan); DLQ persistence widened (backend present ⇒ dead letters
  persist without `dead_letter` config); conformance kit dogfooded by both first-party backends.
  Original spec summary follows.
  Durability persistence becomes cohesive per-technology BACKENDS: `waku.backends.sqlalchemy` →
  `SqlAlchemyBackend.register(session_factory=...)` — a `DynamicModule(is_global=True)` providing the resource key +
  `IUnitOfWork` + TWO sibling store objects (`IDurabilityStore` with `.outbox`/`.inbox`/`.dead_letters` facets, home
  `waku.messaging.durability`; cohesive `IEventStore` with `.snapshots`/`.checkpoints` facets, home
  `waku.eventsourcing.store`), `Has(Config)`-gated (budget: exactly 2). Scheduled stays on the inbox facet (NAMED
  divergence from Wolverine's separate `IScheduledMessages` — gap #18 inbox-resident scheduling). Enrollment-by-
  construction: no Enroll/Integrate ceremony analog, ever. Deletes ALL coherence validation (session-identity check,
  `.session` properties, even the override guard — explicit provider override = explicit ownership); provisioning is
  deferred to #12, not deleted. Third-party authors get the **`waku.backends.testing` conformance kit**
  (`MessageStoreCompliance` analog — same-resource + append/forward-atomicity + facet conformance). Also absorbs the
  M7 flag-2 UoW-guard question (fail-loud lives at `OnModuleRegistration`, before dishka's eager validation). Seats
  reserved: provisioning ext (#12), storage-admin facet (#14), node/ownership store (election). Lands as slice
  **#21 after visibility-migration (#20)**; plan just-in-time post-#20; docs slices 17-19 document the pre-B surface,
  B carries its own docs delta (accepted pre-1.0 cost). Prep: `.research/2026-07-10-durability-assembly-prep.md` +
  architect research `.research/2026-07-10-backend-module-architecture-research.md`. Rationale: the SPI shape is
  hard-to-revoke — ship 1.0 with the final backend-author contract, and stop batching deferred-clean into rewrite
  campaigns like the current fix queue.
- **Leader election / relay-node-ownership (gap #6) is COMMITTED pre-v1.0** — must land before a full release. Topology is
  ONE shared DB with services in 2-3 DCs on k8s, so concurrent relays are the live reality. **The I2 per-group-FIFO
  divergence is FIXED by A1 (2026-07-10, `0986538`, uncommitted-to-remote):** non-terminal head occupancy (a `PROCESSING`
  head blocks its group's successors until terminal) gives cluster-wide per-group FIFO under concurrent relays today,
  bounded by `stuck_threshold` (a live send outrunning the threshold reopens the at-least-once duplicate window); outbox
  head key is now composite `(group_id, destination)`. What remains for gap #6 is **liveness** (dead-node recovery faster
  than `stuck_threshold`, Wolverine Balanced node-ownership), not FIFO correctness. Detailed design pending. Gap #31
  (Balanced preset) depends on it. (Gap #6 stays in its M4+ home structurally; the pre-v1.0 commitment is recorded here.)
- **Per-rule requeue budgets (I3) APPROVED + planned** — `Requeue(maxAttempts)` per rule (vs today's collapse to the endpoint
  bound); folded into the gap #32 error-builder umbrella. Plan: `.research/2026-07-04-error-policy-requeue-budgets-plan.md`.
- **Broker-native TTL — DEFERRED** — expiry rides as headers only today; no `x-message-ttl` / per-message broker TTL. Not promoted.
- **ES hard delete (GDPR) — stays TRACKED** as gap #42 (not declined).

#### M3 — Developer Experience + Ops Completeness (20 capabilities / 13 entries)

- [ ] **gap #25 — `EndpointFor(name/uri)` explicit addressing** — imperative send/publish to a specific endpoint; per-URI home already exists post unified-endpoint model — [bus]
- [ ] **gap #26 — Envelope `Source` / service provenance** — inbound messages carry no origin; prerequisite/companion to gap #24 CloudEvents `source` — [bus]
- [ ] **gap #27 — `PreviewSubscriptions` routing dry-run** — no routing-preview diagnostics; DX/ops introspection (pairs with `describe_pipeline`) — [bus]
- [ ] **gap #28 — per-message `DeduplicationId` / `PartitionKey` options** — no per-call dedup-id / partition-key override; extends the shipped DeliveryOptions (gap #18) — [bus]
- [ ] **gap #29 — RabbitMQ exchange/topic routing + `BroadcastToTopicAsync`** — direct-to-queue only, no exchange pub/sub or topic fan-out; **largest M3 item — requires a URI-grammar extension** (topic/exchange/routing-key) + mapper recovery; builds on the per-URI endpoint/mapper home — [transport + bus]
- [ ] **gap #30 — `IdAndDestination` dedup mode** — expose per-listener idempotency as a selectable mode (the inbox already dedups on the composite `(message_id, handler-FQN)`) — [durability]
- [ ] **gap #31 — named durability presets (Solo/Balanced/…)** — composition-only today; Solo/Serverless are config sugar, **Balanced gated on leader election (gap #6, committed pre-v1.0)** — [durability]
- [ ] **gap #32 — Error Policy Builder Completeness (umbrella — design the builder once)** — one pass extending the `ErrorPolicy` shape: `Fault<T>` terminal-failure events; **ScheduleRetry** (non-blocking delayed retry, frees the worker slot vs inline backoff sleeps); custom/compensating continuations on terminal failure; inner-exception / OR-of-types matching (wrapped DB/ORM exceptions); chain-policy scoping (predicate-selected handlers); compound `.And`/`.Then` continuations; selectable jitter strategies; **folds the APPROVED per-rule requeue budgets (I3)** — [error]
- [ ] **gap #33 — listener receive-loop health / auto-restart** — a wedged broker listener is neither self-healed nor surfaced — [transport]
  - **Resilience symmetry set: #33 ⇄ #15 ⇄ relay-health(#6) — design together** (the M8 `ListeningAgent` spec's anti-drift rule; the listen-side and send-side halves must not silently diverge again).
  - M8 deferred-capabilities ledger (from the M8 spec §5 / plan §7 — M8 deliberately did NOT build these):

    | Capability (Wolverine analog) | Deferred to | Send-side sibling | Rule |
    |---|---|---|---|
    | `restart(force)` stuck-listener remediation | **gap #33** (listener health/auto-restart, M3 triage) | outbox relay health (election pkg, gap #6) | design #33 + relay-health in ONE session |
    | Inbox-health pause + probe (`PauseForInboxRecoveryAsync`) | **gap #33** | relay's DB-unavailable behavior | same |
    | `latch_permanently()` + `GLOBALLY_LATCHED` | **gap #33** | outbox pause-sending action | same |
    | Listener circuit-breaker (moves into agent — M8) | — (M8) | **gap #15** sending-failure CB (M3) | #15's design MUST read spec §3.3's CB table first |
    | Unified per-URI health/registry (`EndpointCollection.CollectEndpointHealth`) | future layer over A (election era) | same layer | triggered by `waku_nodes`, not before |
    | Parallel listeners per URI (`ListenerCount`→`ParallelListener`) | recorded divergence, no gap yet (superseded by broker-side concurrency + processing `max_parallel`) | send side N/A | revisit only on a concrete >1-consumer-per-URI-per-node case |
    | Manual operator pause/resume | **gap #33** | relay pause-sending | same session as #33 |
- [ ] **gap #34 — per-handler success/processing log levels + telemetry opt-out** — outcome→level map is hardcoded; extends the gap #20 observer seam — [es-obs]
- [ ] **gap #35 — `AuditMembersPolicy` (base-type-wide audit)** — audit is per-field/per-type only; extends the gap #20 `Audit()` mechanism — [es-obs]
- [ ] **gap #36 — observer hooks for Received / NoHandlerFor / NoRoutesFor** — dropped/unroutable messages are invisible to observers; small extension of the gap #20 `IMessageObserver` surface — [es-obs]
- [ ] **gap #37 — inline projection rebuild** — a corrupted inline read model needs hand-written replay; the ES-projection arm of the Storage Admin rebuild tooling (gap #14) — [es-marten]
- [ ] **gap #46 — batch poison-item isolation + coalescing + batch context (Wolverine GH-3289, 6.17)** — expands gap #11 scope: throw-to-isolate poison members (`ApplyItemException` analog), count-based `IsolateBatchMembers`/probe-individually (verb belongs in the gap #32 error-builder umbrella), `CoalesceBy` last-write-wins dedup, batch-identity context, direct-handler-shadows-batch startup check — design gap #11 with ALL of this in scope, once — [error+bus]

#### M4+ — Advanced Patterns (7 capabilities)

- [ ] **gap #38 — streaming responses (`StreamAsync<T>`)** — `invoke` is single-response; needs a streaming-response substrate Waku deliberately lacks today — [bus]
- [ ] **gap #39 — `ISendMyself` / self-routing cascades** — no custom per-message routing from handler returns; advanced cascade extensibility — [bus]
- [ ] **gap #40 — inbox status-partitioning (perf)** — high-throughput inbox optimization; **gate on a benchmark** before building (matches the pipeline-precomposition stance) — [durability]
- [ ] **gap #41 — pessimistic stream locking (`FetchForExclusiveWriting`)** — optimistic-retry only; contention = retry storms — [es-marten]
- [ ] **gap #42 — hard delete of streams (GDPR erasure)** — soft/archive-delete only, no physical erasure; **stays tracked (dex)**; touches store immutability + snapshots + projections — [es-marten]
- [ ] **gap #43 — ES multi-tenancy (tenant on streams/events)** — the M4+ tenancy item is messaging-framed; the ES side is untracked — pairs with it — [es-marten]
- [ ] **gap #44 — event/stream querying surface** — `read_stream` / `read_all` only; no richer event/stream query API — [es-marten]
- [ ] **gap #47 — explicit transactional-store selection for multi-store handlers** (Wolverine 6.17 `TransactionalAttribute.DbContextType` analog) — a handler touching >1 SQLAlchemy session: which one `TransactionalBehavior` commits; niche, candidate-only — [durability]

#### Declined (1 capability)

- **gap #45 — indefinite / uncapped retry/requeue** — [error] — **CONFLICTS with a committed sound divergence.** Waku commits
  to bounded budgets + a poison ceiling: gap #17 `max_requeue_attempts` → DLQ, `SendingFailurePolicy` requires an explicit
  terminal so a durable message is never silently dropped, and the approved I3 requeue budgets are *bounded*. Unbounded retry
  contradicts that terminal-guarantee invariant. Revisit only for a concrete unbounded-transient-infra case — not tracked as planned work.

---

## M4+ — Advanced Patterns [FUTURE]

### Process Managers / Sagas

Long-running stateful workflows.

- [ ] Saga base class with state persistence
- [ ] Process manager (choreography-based, event handler + state)
- [ ] Integration with inbox/outbox for transactional state updates

### Temporal Integration Adapter

Bridge for complex workflows, human-in-the-loop, compensation.

- [ ] Thin adapter between Waku message bus and Temporal activities
- [ ] NOT a saga engine — Temporal handles orchestration

### Multi-Tenancy

Tenant-aware messaging.

- [ ] TenantId on envelope / delivery options
- [ ] Per-tenant routing
- [ ] Tenant propagation to cascading messages

### Request/Reply Across Transports

Synchronous request-response over async transports.

- [ ] Dedicated response queues per node
- [ ] Correlation-based response matching

### Subscriptions / Event Streaming

Event store → message bus bridging.

- [ ] Subscribe to event stream changes
- [ ] Publish events to handlers via bus

### DurabilityAgent + Leader Election (gap #6, full)

Full multi-node coordination. Builds on `waku_nodes` (M3).

- [ ] Advisory-lock-based leadership election (PostgreSQL `pg_advisory_lock`)
- [ ] Leader-only orphan recovery + agent assignment
- [ ] Stale node detection + graceful eviction
- [ ] Replaces/supplements `FOR UPDATE SKIP LOCKED` recovery from M1b

### Exclusive Node Processing (gap #13)

- [ ] `exclusive_node` flag on endpoint config
- [ ] Cluster-wide single-consumer with failover
- [ ] Requires DurabilityAgent

**Design note (2026-07-01, surfaced during the unified-endpoint-model design).** This "exclusive_node flag" is
Wolverine's `ListenerScope` enum — `CompetingConsumers` vs `Exclusive` (Wolverine 3.0 plans to fold the `IsListener`
bool *into* it via an `Off` value; source: `Endpoint.cs`, commit `feba5cd`). Planning implications:
- **Config home = a `scope: ListenerScope` field *inside* `ListenAspect`** on the unified `Endpoint`, NOT a flat flag.
  This **depends on the unified bidirectional Endpoint model landing first** (see the `2026-07-01` unified-endpoint
  design spec / gap for the endpoint-model unification). The aspect is where the field composes cleanly; without the
  unified model, `exclusive_node` config hits the same inbound/outbound fragmentation the endpoint refactor fixes.
  We deliberately chose aspects over a flat `ListenerScope` field partly to reserve this slot at zero cost today.
- **Enforcement is a fork to decide when this is planned:** (a) **leader election / DurabilityAgent (gap #6)** — elect
  one node to own the exclusive listener; gives failover, transport-agnostic. Or (b) **broker-native exclusive consume**
  — RabbitMQ exclusive-consumer flag / Kafka single-partition assignment — single-consumer with NO election needed
  (cheaper, no gap #6 dependency, but transport-specific and weaker failover). Waku may want both, per transport.
- **Baseline:** Waku is competing-consumers by construction today (`FOR UPDATE SKIP LOCKED` inbox + consumer groups —
  Slice C shipped with no Solo/Balanced dial). Exclusive is a deliberate departure, not a default.

---

## Architecture Decisions Index

Decisions made during design that inform future work.

| Decision | Summary | Doc |
|----------|---------|-----|
| Error policies are endpoint-level | ErrorPolicyEvaluator at endpoint layer | Memory: `decision_error_policies_transport.md` |
| ErrorPolicy builder API | `ErrorPolicy.on_exception(Exc).retry_with_backoff(...).then_move_to_dead_letter()` | Memory: `decision_retry_policy_builder.md` |
| Error handling + DLQ | EndpointExecutor + Wolverine move_to_dead_letter model | Memory: `decision_error_handling_arch.md` |
| Transaction ownership | Stores never commit, scope owner commits | Memory: `decision_transaction_ownership.md` |
| Message type naming | Explicit alias with FQN fallback | Memory: `decision_message_type_naming.md` |
| OutboxConfig grouping | Store+transport+relay in one config | Memory: `decision_outbox_config_grouping.md` |
| Exception hierarchy | ImproperlyConfiguredError as framework-wide config base | Memory: `decision_exception_hierarchy.md` |
| Cascading messages | CascadingBehavior flushes in handler scope (Design B); bus unchanged | Memory: `decision_cascading_messages.md` |
| Inbox pattern | Write-ahead persistence, PK dedup | Memory: `decision_inbox_pattern.md` |
| Trace context | Via envelope headers (not dedicated column) | Decided 2026-03-31 in design session |

---

## Reference Systems

| System | Role | Path                                                              |
|--------|------|-------------------------------------------------------------------|
| Wolverine (.NET) | Primary reference for messaging patterns | [wolverinefx.net](https://wolverinefx.net) and `~/Code/wolverine/` |
| Stukachok | Production system Waku replaces | External: `tochka/stukachok/`                                     |
| Marten (.NET) | Reference for event sourcing patterns | [martendb.io](https://martendb.io)                                |
