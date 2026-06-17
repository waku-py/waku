from __future__ import annotations

import asyncio
import contextlib
import enum
import time
from collections import deque
from typing import TYPE_CHECKING

import anyio

from waku.messaging.endpoints.executor import ExecutionOutcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig

__all__ = [
    'CircuitBreaker',
    'CircuitState',
]

_FAILURE_OUTCOMES = frozenset({
    ExecutionOutcome.DEAD_LETTERED,
    ExecutionOutcome.DEAD_LETTER_FAILED,
    ExecutionOutcome.FAILED_NO_POLICY,
})


@enum.unique
class CircuitState(enum.Enum):
    CLOSED = 'CLOSED'
    OPEN = 'OPEN'


class CircuitBreaker:
    """Per-endpoint, rate-based circuit breaker. CLOSED → OPEN(pause) → resume+reset → CLOSED.

    Records one data-point per message (terminal `ExecutionOutcome`); `DISCARDED` is not recorded.
    Trips when, over `tracking_period`, total ≥ `minimum_throughput` AND failures/total >
    `failure_rate_threshold`. `now`/`sleep` are injected for deterministic tests.
    """

    __slots__ = (
        '_config',
        '_lock',
        '_now',
        '_pause',
        '_resume',
        '_resume_task',
        '_sleep',
        '_state',
        '_window',
    )

    def __init__(
        self,
        *,
        config: CircuitBreakerConfig,
        pause: Callable[[], Awaitable[None]],
        resume: Callable[[], Awaitable[None]],
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._config = config
        self._pause = pause
        self._resume = resume
        self._now = now
        self._sleep = sleep
        self._state = CircuitState.CLOSED
        self._window: deque[tuple[float, bool]] = deque()
        self._resume_task: asyncio.Task[None] | None = None
        # Serializes concurrent record() calls — BUFFERED endpoints run max_parallel consumers that
        # each call record() on the SAME breaker; without this two could both observe should_trip()
        # and double-trip (orphaning a resume task). The lock makes the check-and-trip atomic.
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def record(self, outcome: ExecutionOutcome, exc: Exception | None) -> None:
        if outcome is ExecutionOutcome.DISCARDED:
            return
        async with self._lock:
            is_failure = outcome in _FAILURE_OUTCOMES and self._exc_counts(exc)
            now = self._now()
            self._window.append((now, is_failure))
            self._evict(now)
            if self._state is CircuitState.CLOSED and self._should_trip():
                await self._trip()

    def _exc_counts(self, exc: Exception | None) -> bool:
        if exc is None:
            return False
        track, ignore = self._config.track_exceptions, self._config.ignore_exceptions
        if ignore and isinstance(exc, ignore):
            return False
        return not track or isinstance(exc, track)

    def _evict(self, now: float) -> None:
        cutoff = now - self._config.tracking_period.total_seconds()
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def _should_trip(self) -> bool:
        total = len(self._window)
        if total < self._config.minimum_throughput:
            return False
        failures = sum(1 for _, failed in self._window if failed)
        return failures / total > self._config.failure_rate_threshold

    async def _trip(self) -> None:
        self._state = CircuitState.OPEN
        await self._pause()
        self._resume_task = asyncio.create_task(self._run_resume())

    async def _run_resume(self) -> None:
        await self._sleep(self._config.pause_time.total_seconds())
        # Reset state + window BEFORE un-pausing the worker, so the first post-resume record() starts
        # from a clean window (spec §3 order: clear → CLOSED → resume). These sync statements run
        # atomically w.r.t. the event loop; the worker stays paused until resume() returns.
        self._window.clear()
        self._state = CircuitState.CLOSED
        await self._resume()
        # Clear the slot only if we are still the active task: if resume() yielded and a fresh trip
        # replaced _resume_task, clobbering it here would orphan the new task past aclose().
        if self._resume_task is asyncio.current_task():
            self._resume_task = None

    async def wait_for_resume(self) -> None:
        # Test helper: await the pending resume task (if any) to completion. Capture locally — the
        # task clears self._resume_task on completion.
        task = self._resume_task
        if task is not None:
            await task

    async def aclose(self) -> None:
        # Cancel a pending resume on endpoint shutdown.
        if self._resume_task is not None:
            self._resume_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._resume_task
            self._resume_task = None
