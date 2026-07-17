from __future__ import annotations

import abc
import asyncio
import contextlib
import enum
import time
from collections import deque
from typing import TYPE_CHECKING, Final

import anyio
from typing_extensions import override

from waku.messaging.endpoints.outcome import ExecutionOutcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig

__all__ = [
    'CircuitBreaker',
    'CircuitState',
]

_FAILURE_OUTCOMES: Final[frozenset[ExecutionOutcome]] = frozenset({
    ExecutionOutcome.DEAD_LETTERED,
    ExecutionOutcome.DEAD_LETTER_FAILED,
    ExecutionOutcome.FAILED_NO_POLICY,
})


@enum.unique
class CircuitState(enum.Enum):
    CLOSED = 'CLOSED'
    OPEN = 'OPEN'


class ICircuitBreaker(abc.ABC):
    """Minimal breaker seam the endpoints depend on: sample an execution outcome, and close on shutdown.

    Kept to exactly what consumers call (ISP) so a null sibling (``PassthroughCircuitBreaker``) can stand
    in for ``None`` when an endpoint has no ``circuit_breaker_config`` — the endpoint feeds every outcome to
    ``record`` unconditionally and never branches on the breaker's absence.
    """

    __slots__ = ()

    @abc.abstractmethod
    async def record(self, outcome: ExecutionOutcome, exc: Exception | None) -> None: ...

    @abc.abstractmethod
    async def aclose(self) -> None: ...


class CircuitBreaker(ICircuitBreaker):
    """Per-endpoint, rate-based circuit breaker. CLOSED → OPEN(pause) → resume+reset → CLOSED.

    One sample per handler-execution. `DISCARDED` is not recorded; `REQUEUED`/`PAUSED` record as
    NEUTRAL — the message is re-sampled on its eventual terminal outcome (counting them as failures
    would double-pause via a trip). Trips when total ≥ `minimum_throughput` AND
    failures/total >= `failure_rate_threshold` within `tracking_period`. `now`/`sleep` are injected.
    """

    __slots__ = (
        '_config',
        '_lock',
        '_now',
        '_pause',
        '_pause_token',
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
        pause: Callable[[], Awaitable[PauseToken]],
        resume: Callable[[PauseToken], Awaitable[None]],
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
        self._pause_token: PauseToken | None = None
        # BUFFERED endpoints run max_parallel consumers all calling record() on the same breaker;
        # without the lock two could both observe should_trip() and double-trip (orphaning a resume
        # task). Makes the check-and-trip atomic.
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @override
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
        return failures / total >= self._config.failure_rate_threshold

    async def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._pause_token = await self._pause()
        self._resume_task = asyncio.create_task(self._run_resume())

    async def _run_resume(self) -> None:
        await self._sleep(self._config.pause_time.total_seconds())
        # Clear window + set CLOSED BEFORE resuming: first post-resume record() starts from a clean
        # window. Sync statements run atomically w.r.t. the event loop; worker stays paused until
        # resume() returns.
        self._window.clear()
        self._state = CircuitState.CLOSED
        # Release ONLY the token this trip minted — a coexisting hold (e.g. a PAUSE action) keeps
        # the gate closed. Refcounted resume, not an unconditional un-pause.
        token, self._pause_token = self._pause_token, None
        if token is not None:
            await self._resume(token)
        # Guard against clobbering a newer _resume_task spawned while resume() yielded.
        if self._resume_task is asyncio.current_task():
            self._resume_task = None

    async def wait_for_resume(self) -> None:
        # Test helper. Capture locally — the task clears _resume_task on completion.
        task = self._resume_task
        if task is not None:
            await task

    @override
    async def aclose(self) -> None:
        if self._resume_task is not None:
            self._resume_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._resume_task
            self._resume_task = None


class PassthroughCircuitBreaker(ICircuitBreaker):
    """Always-closed null breaker used when an endpoint has no ``circuit_breaker_config`` — both methods no-op."""

    __slots__ = ()

    @override
    async def record(self, outcome: ExecutionOutcome, exc: Exception | None) -> None: ...

    @override
    async def aclose(self) -> None: ...
