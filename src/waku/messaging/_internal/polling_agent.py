from __future__ import annotations

import abc
import asyncio
import contextlib
import enum
import logging
import random
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, runtime_checkable

import anyio
from typing_extensions import override

from waku._internal.adaptive_interval import AdaptiveInterval
from waku._internal.polling import PollingConfig
from waku._internal.transaction import (
    TransactionExecutionError,
    can_defer_transaction_fatal,
    extract_transaction_execution_error,
    fatal_carries_control_flow,
)

if TYPE_CHECKING:
    from datetime import timedelta

__all__ = [
    'DEFAULT_DURABILITY_POLLING_CONFIG',
    'AdaptivePace',
    'FixedPace',
    'IPaceStrategy',
    'Placement',
    'PollingAgent',
    'Throttle',
    'log_fatal_task_death',
]

logger = logging.getLogger(__name__)

# Shared background cadence for the durability poll-loop agents (outbox relay, dead-letter worker):
# slower than the projection default so idle nodes don't hammer the store. The ONE authority both the
# ``OutboxRelayConfig`` and ``DeadLetterConfig`` polling defaults resolve to.
DEFAULT_DURABILITY_POLLING_CONFIG: Final = PollingConfig(
    poll_interval_min_seconds=1.0,
    poll_interval_max_seconds=30.0,
    poll_interval_step_seconds=1.0,
    poll_interval_jitter_factor=0.1,
)


def log_fatal_task_death(task: asyncio.Task[None], owner: str, *, task_logger: logging.Logger) -> None:
    """Surface a durability task's fatal death at CRITICAL, so it is visible in flight, not only at shutdown.

    Cancellation and clean completion are normal shutdown and stay silent; a fatal transaction signal
    reports its underlying error under the owner's own logger.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is None:
        return
    fatal = extract_transaction_execution_error(error)
    task_logger.critical(
        '%s terminated with an unrecovered fatal error',
        owner,
        exc_info=fatal.error if fatal is not None else error,
    )


@runtime_checkable
class IPaceStrategy(Protocol):
    def record(self, processed: int) -> None: ...

    def next_delay(self) -> float: ...


class AdaptivePace(IPaceStrategy):
    """Collapses to min interval after a productive tick; grows toward max while idle."""

    __slots__ = ('_interval',)

    def __init__(self, config: PollingConfig) -> None:
        self._interval = AdaptiveInterval(
            min_seconds=config.poll_interval_min_seconds,
            max_seconds=config.poll_interval_max_seconds,
            step_seconds=config.poll_interval_step_seconds,
            jitter_factor=config.poll_interval_jitter_factor,
        )

    @override
    def record(self, processed: int) -> None:
        if processed > 0:
            self._interval.on_work_done()
        else:
            self._interval.on_idle()

    @override
    def next_delay(self) -> float:
        return self._interval.current_with_jitter()


class FixedPace(IPaceStrategy):
    """Constant-interval pacing; ignores work count.

    ``jitter_factor`` adds per-node randomness (used by scheduled-promotion poll to avoid multi-pod
    SKIP-LOCKED contention); default 0.0 is exact.
    """

    __slots__ = ('_jitter_factor', '_seconds')

    def __init__(self, seconds: float, jitter_factor: float = 0.0) -> None:
        self._seconds = seconds
        self._jitter_factor = jitter_factor

    @override
    def record(self, processed: int) -> None:
        return

    @override
    def next_delay(self) -> float:
        if not self._jitter_factor:
            return self._seconds
        return self._seconds * random.uniform(1 - self._jitter_factor, 1 + self._jitter_factor)  # noqa: S311


class Throttle:
    """Monotonic time-gate: admits an action at most once per ``interval`` seconds.

    Tracks the last pass on the ``time.monotonic()`` clock; ``ready`` returns True and resets the
    window only once at least ``interval`` seconds have elapsed since the previous pass. Shared by
    every agent whose tick carries a slower secondary duty than its poll cadence.
    """

    __slots__ = ('_interval', '_last_run')

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._last_run = 0.0

    def ready(self, now: float) -> bool:
        if now - self._last_run < self._interval:
            return False
        self._last_run = now
        return True


class Placement(enum.Enum):
    """Deployment-topology marker. Documentation/wiring hook only — runtime never branches on it."""

    SINGLETON_PER_DC = 'SINGLETON_PER_DC'
    PER_POD = 'PER_POD'


class PollingAgent(abc.ABC):
    """Base for durability poll-loop agents (outbox relay, inbox recovery, dead-letter worker).

    ``start`` spawns ``_run_loop``; ``stop`` signals + joins + cancels. Each cycle calls ``_tick``,
    paces via the subclass strategy, and waits interruptibly. ``_tick`` exceptions log-and-continue.
    """

    __slots__ = ('_pace', '_shutdown_event', '_stop_timeout', '_worker_task')

    placement: ClassVar[Placement]

    retries_after_fatal: ClassVar[bool] = False
    """Whether a fatal transaction signal is retried on the next tick instead of ending the loop.

    Default False: an agent that cannot commit stops, and ``_on_worker_done`` reports the death at
    CRITICAL — for a maintenance duty a visible stall beats a silently degraded loop. An agent whose
    silence is itself harmful sets this True. It is never honoured for a fatal that wraps or surfaced
    alongside a control-flow ``BaseException``, so retrying can never demote cancellation.
    """

    def __init__(self, *, stop_timeout: timedelta) -> None:
        self._stop_timeout = stop_timeout
        self._pace = self._make_pace()
        self._shutdown_event = anyio.Event()
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def is_stopped(self) -> bool:
        return self._worker_task is None

    @abc.abstractmethod
    def _make_pace(self) -> IPaceStrategy: ...

    @abc.abstractmethod
    async def _tick(self) -> int: ...

    async def start(self) -> None:
        if self._worker_task is not None:
            msg = f'{type(self).__name__} is already started'
            raise RuntimeError(msg)
        # Fresh event per run: anyio.Event is one-shot (no reset), and stop() leaves the old one set —
        # reusing it would make a restarted loop exit before its first tick.
        self._shutdown_event = anyio.Event()
        self._worker_task = asyncio.create_task(self._run_loop())
        self._worker_task.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, task: asyncio.Task[None]) -> None:
        log_fatal_task_death(task, type(self).__name__, task_logger=logger)

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._worker_task is None:
            return
        try:
            with anyio.fail_after(self._stop_timeout.total_seconds()):
                await self._worker_task
        except TimeoutError:
            logger.warning(
                '%s did not terminate within %.1fs, cancelling',
                type(self).__name__,
                self._stop_timeout.total_seconds(),
            )
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            fatal_to_raise: TransactionExecutionError | None = None
            processed = 0
            try:
                processed = await self._tick()
            except BaseException as error:
                if fatal := extract_transaction_execution_error(error):
                    deferrable = can_defer_transaction_fatal(error, fatal)
                    # A retrying agent may swallow a fatal only when no control-flow `BaseException`
                    # reached it — neither beside the fatal in a group (`deferrable or fatal is error`)
                    # nor inside the fatal's own payload (`fatal_carries_control_flow`).
                    retryable = self.retries_after_fatal and not fatal_carries_control_flow(fatal)
                    if (deferrable or fatal is error) and retryable:
                        # ERROR, not CRITICAL: the loop survives, so this is a degraded tick and not the
                        # unrecovered death `_on_worker_done` reports.
                        logger.exception(
                            '%s tick failed with an unrecoverable transaction error, retrying next tick',
                            type(self).__name__,
                        )
                        processed = 0
                    elif deferrable:
                        fatal_to_raise = fatal
                    else:
                        raise
                elif not isinstance(error, Exception):
                    raise
                else:
                    logger.exception('%s tick failed, continuing loop', type(self).__name__)
                    processed = 0
            if fatal_to_raise is not None:
                raise fatal_to_raise
            self._pace.record(processed)
            with anyio.move_on_after(self._pace.next_delay()):
                await self._shutdown_event.wait()
