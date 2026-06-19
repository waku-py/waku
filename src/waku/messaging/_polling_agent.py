from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

import anyio
from typing_extensions import override

from waku._internal.adaptive_interval import AdaptiveInterval

if TYPE_CHECKING:
    from waku._internal.polling import PollingConfig

__all__ = [
    'AdaptivePace',
    'FixedPace',
    'IPaceStrategy',
    'Placement',
    'PollingAgent',
]

logger = logging.getLogger(__name__)


@runtime_checkable
class IPaceStrategy(Protocol):
    def record(self, processed: int) -> None: ...

    def next_delay(self) -> float: ...


class AdaptivePace(IPaceStrategy):
    """Work-adaptive pacing: collapses to the min interval after a productive tick.

    Grows toward max while idle. Wraps `AdaptiveInterval` built from a `PollingConfig`.
    """

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
    """Constant-interval pacing (the recovery worker's fixed `recovery_interval`); ignores work count."""

    __slots__ = ('_seconds',)

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    @override
    def record(self, processed: int) -> None:
        return

    @override
    def next_delay(self) -> float:
        return self._seconds


class Placement(enum.Enum):
    """Deployment-topology marker for a polling agent.

    Documentation/wiring hook ONLY — the runtime never branches on it (INVARIANT 5: 1-per-DC topology
    is enforced by deployment, not by code).
    """

    SINGLETON_PER_DC = 'SINGLETON_PER_DC'
    PER_POD = 'PER_POD'


class PollingAgent(ABC):
    """Internal base for poll-loop durability agents (outbox relay, inbox recovery, dead-letter worker).

    Owns the background-task lifecycle: `start` spawns `_run_loop`, `stop` signals + joins + cancels.
    `_run_loop` is a template — each cycle runs `_tick`, paces from the subclass strategy, and waits
    interruptibly on the shutdown event. A `_tick` exception logs-and-continues (never breaks the loop).
    Subclasses provide `_tick`, `_make_pace`, and a `placement` marker; their own `__init__` resolves
    domain collaborators before calling `super().__init__(stop_timeout=...)`.
    """

    __slots__ = ('_pace', '_shutdown_event', '_stop_timeout', '_worker_task')

    placement: ClassVar[Placement]

    def __init__(self, *, stop_timeout: float) -> None:
        self._stop_timeout = stop_timeout
        self._pace = self._make_pace()
        self._shutdown_event = anyio.Event()
        self._worker_task: asyncio.Task[None] | None = None

    @abstractmethod
    def _make_pace(self) -> IPaceStrategy: ...

    @abstractmethod
    async def _tick(self) -> int: ...

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._worker_task is None:
            return
        try:
            with anyio.fail_after(self._stop_timeout):
                await self._worker_task
        except TimeoutError:
            logger.warning('%s did not terminate within %.1fs, cancelling', type(self).__name__, self._stop_timeout)
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                processed = await self._tick()
            except Exception:
                logger.exception('%s tick failed, continuing loop', type(self).__name__)
                processed = 0
            self._pace.record(processed)
            with anyio.move_on_after(self._pace.next_delay()):
                await self._shutdown_event.wait()
