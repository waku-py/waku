from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Final

import anyio
from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.lease import ILease
from waku._internal.transaction import TransactionCleanupError, unit_of_work_scope
from waku.di import is_registered
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import AfterApplicationInit, OnApplicationShutdown
from waku.messaging._internal.polling_agent import AdaptivePace, FixedPace, Placement, PollingAgent
from waku.messaging._internal.transaction import CompletedExecutionError
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.errors._internal.replay import IReplayExecution
from waku.messaging.sequence import ISequenceAllocator

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.application import WakuApplication
    from waku.messaging.config import DeadLetterConfig, LeadershipConfig, MessagingConfig
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.outbox.relay import OutboxRelayConfig

__all__ = [
    'DurabilityMaintenanceAgent',
    'DurabilityMaintenanceLifecycleExtension',
    'LeadershipCoordinator',
]

logger = logging.getLogger(__name__)

# Per-node jitter carried over from ScheduledPromotionWorker: multi-pod FOR UPDATE SKIP LOCKED
# claims don't stomp (Wolverine parity). Harmless under a single leader-owned agent.
_PROMOTION_JITTER_FACTOR: Final[float] = 0.1


class _OutboxMaintenancePoller(PollingAgent):
    """Outbox recovery-sweep + dispatched-cleanup, split off the relay's hot dispatch path.

    Bodies moved verbatim from ``OutboxRelay``; the relay is now dispatch-only. Never commits inside
    a store — ``unit_of_work_scope`` is the sole transaction-scope owner.
    """

    placement = Placement.SINGLETON_PER_DC

    __slots__ = ('_config', '_container', '_last_cleanup', '_last_recovery')

    def __init__(self, *, container: AsyncContainer, config: OutboxRelayConfig) -> None:
        self._container = container
        self._config = config
        self._last_recovery = 0.0
        self._last_cleanup = 0.0
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        recovered = await self._maybe_recover_stuck()
        purged = await self._maybe_cleanup()
        return recovered + purged

    async def _maybe_recover_stuck(self) -> int:
        now = time.monotonic()
        if now - self._last_recovery < self._config.recovery_interval.total_seconds():
            return 0
        self._last_recovery = now
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            store = await scope.get(IOutboxStore)
            recovered: int = await store.recover_stuck(self._config.stuck_threshold)
        if recovered > 0:
            logger.info('Recovered %d stuck messages', recovered)
        return recovered

    async def _maybe_cleanup(self) -> int:
        if self._config.retention is None:
            return 0
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return 0
        self._last_cleanup = now
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            store = await scope.get(IOutboxStore)
            purged: int = await store.cleanup_dispatched(self._config.retention)
        if purged > 0:
            logger.info('Purged %d dispatched outbox messages older than retention', purged)
        return purged


class _DlqMaintenancePoller(PollingAgent):
    """DLQ auto-replay + purge, subsuming ``DeadLetterWorker`` (now 1-per-cluster under the leader).

    Claims rows via ``claim_replayable`` (``FOR UPDATE SKIP LOCKED``), re-injects through
    signal-preserving replay execution, and commits once per batch. Replay NEVER commits; the poller
    is the sole transaction-scope owner. The claim lock is held
    across the batch; an INLINE destination runs its handler synchronously inside the held lock, so
    keep ``batch_size`` modest if any replayable destination is INLINE.
    """

    placement = Placement.SINGLETON_PER_DC

    __slots__ = ('_config', '_container', '_last_cleanup', '_now')

    def __init__(self, *, container: AsyncContainer, config: DeadLetterConfig, now: Now = utc_now) -> None:
        self._container = container
        self._config = config
        self._now = now
        self._last_cleanup = 0.0
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        await self._maybe_cleanup()
        if self._config.auto_replay_enabled:
            return await self._replay_batch()
        return 0

    async def _replay_batch(self) -> int:
        completed_error: CompletedExecutionError | None = None
        replayed = 0
        try:
            with anyio.CancelScope() as completion_scope:
                async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
                    replayed, completed_error = await self._replay_entries(scope)
                    completion_scope.shield = completed_error is not None
        except TransactionCleanupError:
            raise
        except Exception as finalization_error:
            if completed_error is not None or replayed > 0:
                raise CompletedExecutionError(finalization_error) from completed_error
            raise
        except anyio.get_cancelled_exc_class() as finalization_error:
            if completed_error is not None or replayed > 0:
                raise CompletedExecutionError(finalization_error) from completed_error
            raise
        if completed_error is not None:
            raise completed_error
        return replayed

    async def _replay_entries(self, scope: AsyncContainer) -> tuple[int, CompletedExecutionError | None]:
        store = await scope.get(IDeadLetterStore)
        replayer = await scope.get(IReplayExecution)
        entries = await store.claim_replayable(self._config.batch_size, self._config.max_replay_count)
        replayed = 0
        for entry in entries:
            try:
                if await replayer.replay(entry):
                    replayed += 1
            except CompletedExecutionError as error:
                return replayed + 1, error
            except TransactionCleanupError:
                raise
            except Exception as error:
                if replayed > 0:
                    return replayed, CompletedExecutionError(error)
                raise
            except anyio.get_cancelled_exc_class() as error:
                if replayed > 0:
                    return replayed, CompletedExecutionError(error)
                raise
        return replayed, None

    async def _maybe_cleanup(self) -> None:
        if self._config.retention is None:
            return
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return
        self._last_cleanup = now
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            store = await scope.get(IDeadLetterStore)
            purged = await store.purge(self._now() - self._config.retention)
        if purged > 0:
            logger.info('Purged %d dead letters older than retention', purged)


class _PromotionPoller(PollingAgent):
    """Scheduled-promotion poller, subsuming ``ScheduledPromotionWorker``.

    Promotes due SCHEDULED inbox rows to INCOMING on its own ``scheduled_poll_interval`` cadence —
    independent of the outbox/DLQ pollers' cadence (they run as separate child tasks).
    """

    placement = Placement.PER_POD

    __slots__ = ('_config', '_container', '_now')

    def __init__(self, *, container: AsyncContainer, config: InboxConfig, now: Now = utc_now) -> None:
        self._container = container
        self._config = config
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(self._config.scheduled_poll_interval.total_seconds(), jitter_factor=_PROMOTION_JITTER_FACTOR)

    @override
    async def _tick(self) -> int:
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            store = await scope.get(IInboxStore)
            # Cannot miss: registration requires ISequenceAllocator whenever inbox is active — the
            # poller's own start condition. Keyless rows promote without ever invoking it.
            allocator: ISequenceAllocator = await scope.get(ISequenceAllocator)
            promoted: int = await store.promote_due_scheduled(self._now(), allocator, self._config.batch_size)
        if promoted > 0:
            logger.info('Promoted %d due scheduled inbox entries to INCOMING', promoted)
        return promoted


class DurabilityMaintenanceAgent:
    """Single-owner durability maintenance: outbox recover+cleanup, DLQ replay+purge, scheduled promotion.

    Composes up to three ``PollingAgent`` sub-pollers, each an independent ``asyncio.Task`` with its
    own pace strategy — so the three concerns run CONCURRENTLY with no cross-concern head-of-line
    blocking (a slow INLINE DLQ replay never delays the outbox sweep or scheduled promotion). Only
    the configured subsystems get a poller. Started/stopped as ONE lifecycle unit — unconditionally on
    every node when leadership is off, or gated by the leader's lease when leadership is on.
    """

    __slots__ = ('_pollers',)

    def __init__(self, *, container: AsyncContainer, config: MessagingConfig, now: Now = utc_now) -> None:
        pollers: list[PollingAgent] = []
        if config.outbox is not None:
            pollers.append(_OutboxMaintenancePoller(container=container, config=config.outbox.relay))
        if config.dead_letter is not None and (
            config.dead_letter.auto_replay_enabled or config.dead_letter.retention is not None
        ):
            pollers.append(_DlqMaintenancePoller(container=container, config=config.dead_letter, now=now))
        if config.inbox is not None:
            pollers.append(_PromotionPoller(container=container, config=config.inbox, now=now))
        self._pollers = tuple(pollers)

    @property
    def pollers(self) -> tuple[PollingAgent, ...]:
        return self._pollers

    async def start(self) -> None:
        for poller in self._pollers:
            await poller.start()

    async def stop(self) -> None:
        for poller in reversed(self._pollers):
            await poller.stop()


async def _build_maintenance_agent(app: WakuApplication, config: MessagingConfig) -> DurabilityMaintenanceAgent:
    now = await app.container.get(Now)
    return DurabilityMaintenanceAgent(container=app.container, config=config, now=now)


class DurabilityMaintenanceLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    """No-leader owner (leadership is None): starts the maintenance agent unconditionally on every node."""

    __slots__ = ('_agent', '_config')

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config
        self._agent: DurabilityMaintenanceAgent | None = None

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        self._agent = await _build_maintenance_agent(app, self._config)
        await self._agent.start()

    @override
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        if self._agent is not None:
            await self._agent.stop()


class LeadershipCoordinator(AfterApplicationInit, OnApplicationShutdown):
    """Leader-gated owner (leadership is set): runs the maintenance agent only while this node holds the lease.

    Mirrors ``CatchUpProjectionRunner``'s acquire pattern — the agent runs inside the lease's ``acquire``
    context manager, whose body is cancelled (and ABSORBED at the lease's task-group boundary) when the
    lease is stolen or expires. Control then resumes just past the ``async with``, off the cancellation
    path, where the agent is stopped and the loop retries after one renew interval. Graceful shutdown wakes
    the hold, releasing the lease for immediate handover. Brief dual-ownership during a steal is safe (D1:
    every maintenance operation is SKIP-LOCKED or idempotent), so a plain timestamp lease suffices.
    """

    __slots__ = ('_agent', '_config', '_leadership', '_leading', '_shutdown', '_task')

    def __init__(self, config: MessagingConfig) -> None:
        leadership = config.leadership
        if leadership is None:  # pragma: no cover -- only wired when leadership is set
            msg = 'LeadershipCoordinator requires MessagingConfig.leadership to be set'
            raise ImproperlyConfiguredError(msg)
        self._config = config
        self._leadership: LeadershipConfig = leadership
        self._agent: DurabilityMaintenanceAgent | None = None
        self._leading = False
        self._shutdown = anyio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_leader(self) -> bool:
        """Whether this node currently holds the lease and is running the maintenance agent."""
        return self._leading

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        if not await is_registered(app.container, ILease):
            msg = (
                'MessagingConfig.leadership is configured but no lease provider is available — register a '
                'backend with an ILease provider, e.g. SqlAlchemyBackend.register(..., engine=<AsyncEngine>).'
            )
            raise ImproperlyConfiguredError(msg)
        lease = await app.container.get(ILease)
        agent = await _build_maintenance_agent(app, self._config)
        self._agent = agent
        self._shutdown = anyio.Event()  # fresh event per run: anyio.Event is one-shot
        self._task = asyncio.create_task(self._run_loop(lease, agent))

    @override
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        self._shutdown.set()
        if self._task is None:
            return
        try:
            with anyio.fail_after(self._leadership.stop_timeout.total_seconds()):
                await self._task
        except TimeoutError:
            logger.warning(
                'LeadershipCoordinator did not terminate within %.1fs, cancelling',
                self._leadership.stop_timeout.total_seconds(),
            )
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def _run_loop(self, lease: ILease, agent: DurabilityMaintenanceAgent) -> None:
        role = self._leadership.role
        renew_interval = self._leadership.lease.renew_interval_seconds
        while not self._shutdown.is_set():
            try:
                await self._acquire_and_run(lease, agent, role)
            except Exception:
                logger.exception('Leadership acquire loop failed for role %r, retrying', role)
            if self._shutdown.is_set():
                break
            # Not held, or the lease was just lost: interruptible wait one renew interval, then retry.
            with anyio.move_on_after(renew_interval):
                await self._shutdown.wait()

    async def _acquire_and_run(self, lease: ILease, agent: DurabilityMaintenanceAgent, role: str) -> None:
        started = False
        async with lease.acquire(role) as held:
            if held:
                started = True  # set BEFORE start() so a mid-start steal still stops what began
                await agent.start()
                self._leading = True
                # Held until graceful shutdown (this returns) OR the lease is stolen (this await is
                # cancelled and the cancellation is absorbed by the lease's task group, so control
                # resumes past the `async with` where we stop the agent off the cancellation path).
                await self._shutdown.wait()
        if started:
            self._leading = False
            await agent.stop()
