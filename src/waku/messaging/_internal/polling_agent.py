from __future__ import annotations

import abc
import asyncio
import contextlib
import enum
import logging
import random
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

import anyio
from typing_extensions import override

from waku._internal.adaptive_interval import AdaptiveInterval
from waku._internal.transaction import TransactionExecutionError, extract_transaction_execution_error

if TYPE_CHECKING:
    from datetime import timedelta

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
                    if fatal is error:
                        raise
                    if isinstance(error, BaseExceptionGroup):
                        _, remaining = error.split(TransactionExecutionError)
                        if remaining is None or isinstance(remaining, Exception):
                            fatal_to_raise = fatal
                        else:
                            raise
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
