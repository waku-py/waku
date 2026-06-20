from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from anyio import create_memory_object_stream

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.pauser import PauseRegistry, TimedPauser

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from waku.messaging.pauser import PauseToken

__all__ = [
    'MemoryStreamWorker',
]

logger = logging.getLogger(__name__)


_Handler: TypeAlias = 'Callable[[MessageEnvelope[Any]], Awaitable[None]]'


class MemoryStreamWorker:
    """Memory-stream + background-task lifecycle for an endpoint (GRASP Pure Fabrication).

    ``start(handler)`` runs a bounded pool of ``max_parallel`` consumers. A consumer dequeues
    *before* checking the pause gate, so up to ``max_parallel`` envelopes may be in flight while
    paused. asyncio backend only.
    """

    __slots__ = (
        '_max_buffer_size',
        '_max_parallel',
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
        stop_timeout: float = 5.0,
        max_parallel: int = 1,
    ) -> None:
        self._max_buffer_size = max_buffer_size
        self._stop_timeout = stop_timeout
        self._max_parallel = max_parallel
        self._send_stream: MemoryObjectSendStream[MessageEnvelope[Any]] | None = None
        self._receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._pauses = PauseRegistry()

    async def start(self, handler: _Handler) -> None:
        if self._send_stream is not None:
            msg = 'MemoryStreamWorker is already started'
            raise RuntimeError(msg)
        send, receive = create_memory_object_stream[MessageEnvelope[Any]](
            max_buffer_size=self._max_buffer_size,
        )
        self._send_stream = send
        self._receive_stream = receive
        self._worker_task = asyncio.create_task(self._run(receive, handler))

    async def send(self, envelope: MessageEnvelope[Any]) -> bool:
        send_stream = self._send_stream
        if send_stream is None:
            return False
        try:
            await send_stream.send(envelope)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            return False
        return True

    def try_send(self, envelope: MessageEnvelope[Any]) -> bool:
        # Non-blocking re-enqueue for REQUEUE/PAUSE: blocking here would deadlock when max_parallel=1
        # because the only consumer IS the one trying to re-enqueue.
        send_stream = self._send_stream
        if send_stream is None:
            return False
        try:
            send_stream.send_nowait(envelope)
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
        done, _pending = await asyncio.wait({task}, timeout=self._stop_timeout)
        if task not in done:
            logger.warning(
                'MemoryStreamWorker task did not terminate within %.1fs, cancelling',
                self._stop_timeout,
            )
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception('MemoryStreamWorker task failed during shutdown')

    async def _run(
        self,
        receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]],
        handler: _Handler,
    ) -> None:
        # max_parallel persistent consumers share one stream; each item goes to one waiting receiver.
        # Pool size bounds concurrency and live-task count; backpressure is natural via the buffer.
        async with anyio.create_task_group() as tg:
            for _ in range(self._max_parallel):
                tg.start_soon(self._consume, receive_stream, handler)

    async def _consume(
        self,
        receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]],
        handler: _Handler,
    ) -> None:
        async for envelope in receive_stream:
            await self._pauses.wait()
            try:
                await handler(envelope)
            except Exception:
                logger.exception(
                    'Unhandled error processing message_id=%s, worker continues',
                    envelope.message_id,
                )
