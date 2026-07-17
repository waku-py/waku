from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Final, Never

import anyio
from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.lease import ILease
from waku._internal.transaction import (
    AfterCommitError,
    Commit,
    TransactionDecision,
    TransactionExecutionError,
    can_defer_transaction_fatal,
    extract_transaction_execution_error,
    run_committed,
)
from waku.di import is_registered
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import AfterApplicationInit, OnApplicationShutdown
from waku.messaging._internal.polling_agent import (
    AdaptivePace,
    FixedPace,
    Placement,
    PollingAgent,
    log_fatal_task_death,
)
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayClaimOwner
from waku.messaging.sequence import ISequenceAllocator

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.application import WakuApplication
    from waku.messaging.config import DeadLetterConfig, LeadershipConfig, MessagingConfig
    from waku.messaging.errors.dead_letter import DeadLetterEntry
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

    Bodies moved verbatim from ``OutboxRelay``; the relay is now dispatch-only. Stores never commit;
    each operation is one strict child transaction.
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
        recovered = await self._maybe_recover_abandoned()
        purged = await self._maybe_cleanup()
        return recovered + purged

    async def _maybe_recover_abandoned(self) -> int:
        now = time.monotonic()
        if now - self._last_recovery < self._config.recovery_interval.total_seconds():
            return 0
        self._last_recovery = now

        async def recover(scope: AsyncContainer) -> TransactionDecision[int, Never]:
            store = await scope.get(IOutboxStore)
            return Commit(await store.recover_abandoned(self._config.stuck_threshold))

        recovered = await run_committed(self._container, recover)
        if recovered > 0:
            logger.info('Recovered %d stuck messages', recovered)
        return recovered

    async def _maybe_cleanup(self) -> int:
        retention = self._config.retention
        if retention is None:
            return 0
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return 0
        self._last_cleanup = now

        async def cleanup(scope: AsyncContainer) -> TransactionDecision[int, Never]:
            store = await scope.get(IOutboxStore)
            return Commit(await store.delete_expired_dispatched(retention))

        purged = await run_committed(self._container, cleanup)
        if purged > 0:
            logger.info('Purged %d dispatched outbox messages older than retention', purged)
        return purged


class _DlqMaintenancePoller(PollingAgent):
    """DLQ auto-replay + purge with short leased claim and finalization transactions."""

    placement = Placement.SINGLETON_PER_DC

    __slots__ = ('_config', '_container', '_execution', '_last_cleanup', '_now', '_owner')

    def __init__(self, *, container: AsyncContainer, config: DeadLetterConfig, now: Now = utc_now) -> None:
        self._container = container
        self._config = config
        self._now = now
        self._owner = ReplayClaimOwner(container=container, config=config, now=now)
        self._execution = _ScopedReplayExecution(container)
        self._last_cleanup = 0.0
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        purged = await self._maybe_cleanup()
        if self._config.auto_replay_enabled:
            return purged + await self._replay_batch()
        return purged

    async def _replay_batch(self) -> int:
        fatal_to_raise: TransactionExecutionError | None = None
        replayed = 0
        for _ in range(self._config.batch_size):
            try:
                entry = await self._owner.claim_replayable()
                if entry is None:
                    break
                if await self._owner.replay_claimed(entry, self._execution):
                    replayed += 1
            except BaseException as error:
                if fatal := extract_transaction_execution_error(error):
                    if can_defer_transaction_fatal(error, fatal):
                        fatal_to_raise = fatal
                        break
                    raise
                if not isinstance(error, Exception):
                    raise
                logger.exception('Dead-letter replay transaction failed; keeping committed prefix')
                break
        if fatal_to_raise is not None:
            raise fatal_to_raise
        return replayed

    async def _maybe_cleanup(self) -> int:
        retention = self._config.retention
        if retention is None:
            return 0
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return 0
        self._last_cleanup = now
        sampled_now = self._now()

        async def delete_expired(scope: AsyncContainer) -> TransactionDecision[int, Never]:
            store = await scope.get(IDeadLetterStore)
            return Commit(await store.delete_expired_dead_letters(sampled_now - retention, now=sampled_now))

        purged = await run_committed(self._container, delete_expired)
        if purged > 0:
            logger.info('Purged %d dead letters older than retention', purged)
        return purged


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
        sampled_now = self._now()

        async def promote(scope: AsyncContainer) -> TransactionDecision[int, Never]:
            store = await scope.get(IInboxStore)
            # Cannot miss: registration requires ISequenceAllocator whenever inbox is active — the
            # poller's own start condition. Keyless rows promote without ever invoking it.
            allocator: ISequenceAllocator = await scope.get(ISequenceAllocator)
            return Commit(await store.promote_due_scheduled(sampled_now, allocator, self._config.batch_size))

        promoted = await run_committed(self._container, promote)
        if promoted > 0:
            logger.info('Promoted %d due scheduled inbox entries to INCOMING', promoted)
        return promoted


class _ScopedReplayExecution(IReplayExecution):
    """Keep dispatch collaborators alive while leaving record transactions outside."""

    __slots__ = ('_container',)

    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        dispatch_completed = False
        try:
            async with self._container() as scope:
                execution = await scope.get(IReplayExecution)
                await execution.dispatch(entry)
                dispatch_completed = True
        except BaseException as error:
            if dispatch_completed:
                raise AfterCommitError(error) from error
            raise


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
        # Stop every poller even when one re-raises a stored fatal: a single dead poller must not strand
        # its siblings' shutdown. A lone failure surfaces by identity; genuine multi-failures group.
        errors: list[BaseException] = []
        for poller in reversed(self._pollers):
            try:
                await poller.stop()
            except BaseException as error:  # noqa: BLE001 -- one dead poller must not strand its siblings
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            msg = 'durability maintenance shutdown failed'
            raise BaseExceptionGroup(msg, errors)


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
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        # A fatal from agent.stop() escapes the acquire loop's `except Exception`; surface it in flight
        # rather than let the coordinator task die silently until on_app_shutdown joins it.
        log_fatal_task_death(task, type(self).__name__, task_logger=logger)

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
