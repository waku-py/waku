from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Generic

import anyio
from anyio import create_memory_object_stream
from typing_extensions import TypeVar

from waku._internal.transaction import TransactionExecutionError, extract_transaction_execution_error
from waku.messaging._internal.pauser import PauseRegistry, TimedPauser

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'MemoryStreamWorker',
]

logger = logging.getLogger(__name__)


# Default to MessageEnvelope so buffered queues stay untyped at the construction site; durable queues
# parametrize with their per-handler work item (e.g. tuple[envelope, handlers]).
_ItemT = TypeVar('_ItemT', default='MessageEnvelope[Any]')


class MemoryStreamWorker(Generic[_ItemT]):
    """Memory-stream + background-task lifecycle for an endpoint (GRASP Pure Fabrication).

    ``start(handler)`` runs a bounded pool of ``max_parallel`` consumers over items of type
    ``_ItemT``. A consumer dequeues *before* checking the pause gate, so up to ``max_parallel`` items
    may be in flight while paused. The per-item ``handler`` owns its own error context; ordinary
    message failures never kill the pool, while fatal transaction-cleanup signals stop it. asyncio
    backend only.
    """

    __slots__ = (
        '_max_buffer_size',
        '_max_parallel',
        '_on_drain',
        '_pauses',
        '_receive_stream',
        '_send_stream',
        '_stop_timeout',
        '_worker_task',
    )

    def __init__(
        self,
        *,
        max_buffer_size: float = math.inf,
        stop_timeout: timedelta = timedelta(seconds=5),
        max_parallel: int = 1,
    ) -> None:
        self._max_buffer_size = max_buffer_size
        self._stop_timeout = stop_timeout
        self._max_parallel = max_parallel
        self._send_stream: MemoryObjectSendStream[_ItemT] | None = None
        self._receive_stream: MemoryObjectReceiveStream[_ItemT] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._pauses = PauseRegistry()
        self._on_drain: Callable[[int], Awaitable[None]] | None = None

    @property
    def is_running(self) -> bool:
        return self._send_stream is not None

    @property
    def queue_depth(self) -> int:
        # Buffered items only — the <= max_parallel items already dequeued and in-flight are excluded. That slack is
        # fine for a soft watermark (the listener resumes at most max_parallel items early), and 0 before start().
        receive = self._receive_stream
        return receive.statistics().current_buffer_used if receive is not None else 0

    async def start(
        self,
        handler: Callable[[_ItemT], Awaitable[None]],
        *,
        on_drain: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        if self._send_stream is not None:
            msg = 'MemoryStreamWorker is already started'
            raise RuntimeError(msg)
        self._on_drain = on_drain
        send, receive = create_memory_object_stream[_ItemT](
            max_buffer_size=self._max_buffer_size,
        )
        self._send_stream = send
        self._receive_stream = receive
        self._worker_task = asyncio.create_task(self._run(receive, handler))

    async def send(self, item: _ItemT) -> bool:
        send_stream = self._send_stream
        if send_stream is None:
            return False
        try:
            await send_stream.send(item)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            return False
        return True

    def try_send(self, item: _ItemT) -> bool:
        # Non-blocking re-enqueue for REQUEUE/PAUSE: blocking here would deadlock when max_parallel=1
        # because the only consumer IS the one trying to re-enqueue.
        send_stream = self._send_stream
        if send_stream is None:
            return False
        try:
            send_stream.send_nowait(item)
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            return False
        return True

    async def pause(self) -> PauseToken:
        return self._pauses.pause()

    async def resume(self, token: PauseToken) -> None:
        self._pauses.resume(token)

    def make_timed_pauser(self, *, sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> TimedPauser:
        # Bound to this worker's gate — timed PAUSE and CB pauses compose by refcount.
        return TimedPauser(self._pauses, sleep=sleep)

    async def stop(self) -> None:
        self._pauses.force_resume()  # bypass refcount so a leaked token can't strand shutdown
        send_stream, self._send_stream = self._send_stream, None
        if send_stream is not None:
            send_stream.close()
        if self._worker_task is not None:
            await self._drain_worker(self._worker_task)
            self._worker_task = None
        if self._receive_stream is not None:
            self._receive_stream.close()
            self._receive_stream = None

    async def _drain_worker(self, task: asyncio.Task[None]) -> None:
        # asyncio.wait does NOT propagate cancellation — lets us distinguish a graceful drain from a
        # timeout. anyio.fail_after would cancel the task group inside _run on the deadline.
        done, _pending = await asyncio.wait({task}, timeout=self._stop_timeout.total_seconds())
        if task not in done:
            logger.warning(
                'MemoryStreamWorker task did not terminate within %.1fs, cancelling',
                self._stop_timeout.total_seconds(),
            )
            task.cancel()
        fatal_to_raise: TransactionExecutionError | None = None
        try:
            await task
        except asyncio.CancelledError:
            pass
        except BaseException as error:
            if fatal_error := extract_transaction_execution_error(error):
                if fatal_error is error:
                    raise
                if isinstance(error, BaseExceptionGroup):
                    _, remaining = error.split(TransactionExecutionError)
                    if remaining is None or isinstance(remaining, Exception):
                        fatal_to_raise = fatal_error
                    else:
                        raise
                else:
                    raise
            elif isinstance(error, Exception):
                logger.exception('MemoryStreamWorker task failed during shutdown')
                return
            else:
                raise
        if fatal_to_raise is not None:
            raise fatal_to_raise

    async def _run(
        self,
        receive_stream: MemoryObjectReceiveStream[_ItemT],
        handler: Callable[[_ItemT], Awaitable[None]],
    ) -> None:
        # max_parallel persistent consumers share one stream; each item goes to one waiting receiver.
        # Pool size bounds concurrency and live-task count; backpressure is natural via the buffer.
        async with anyio.create_task_group() as tg:
            for _ in range(self._max_parallel):
                tg.start_soon(self._consume, receive_stream, handler)

    async def _consume(
        self,
        receive_stream: MemoryObjectReceiveStream[_ItemT],
        handler: Callable[[_ItemT], Awaitable[None]],
    ) -> None:
        async for item in receive_stream:
            await self._pauses.wait()
            try:
                await handler(item)
            except TransactionExecutionError:
                raise
            except Exception:
                # Safety net only — items are opaque here, so the handler owns message-level logging.
                logger.exception('Unhandled error in worker consumer, continuing')
            # Post-dequeue depth feeds the low-watermark check (resume the listener once drained).
            # A fatal execution signal stops the worker before this hook: listener/backpressure
            # cleanup must never replace a failed rollback or post-commit teardown error.
            if self._on_drain is not None:
                await self._on_drain(self.queue_depth)
