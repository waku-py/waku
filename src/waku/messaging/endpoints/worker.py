from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from anyio import create_memory_object_stream

from waku.messaging.contracts.envelope import MessageEnvelope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

__all__ = [
    'MemoryStreamWorker',
]

logger = logging.getLogger(__name__)


_Handler: TypeAlias = 'Callable[[MessageEnvelope[Any]], Awaitable[None]]'


class MemoryStreamWorker:
    """Owns the anyio memory stream + background task lifecycle for an endpoint.

    GRASP Pure Fabrication: does not know about handler subscriptions, executor, inbox,
    or routing. Consumers pass an async envelope handler into ``start()`` and ``MemoryStreamWorker``
    runs it with bounded concurrency controlled by ``max_parallel``.

    Concurrency is a bounded pool of ``max_parallel`` consumers sharing one stream. A consumer
    pulls an envelope *before* checking the pause gate, so a paused worker may hold up to
    ``max_parallel`` envelopes in flight (dequeued, not yet dispatched). Uses asyncio primitives
    directly — asyncio backend only.
    """

    __slots__ = (
        '_max_buffer_size',
        '_max_parallel',
        '_paused',
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
        self._paused = asyncio.Event()
        self._paused.set()  # not paused by default

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

    async def pause(self) -> None:
        self._paused.clear()

    async def resume(self) -> None:
        self._paused.set()

    async def stop(self) -> None:
        self._paused.set()  # unblock any paused processors so they can observe shutdown
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
        # asyncio.wait does NOT propagate cancellation into the awaited task, so we can
        # distinguish a graceful drain from a timeout. Awaiting the task directly under
        # anyio.fail_after would cancel the task group inside _run on the deadline, masking
        # the timeout as a clean completion.
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
        # Bounded worker pool: max_parallel persistent consumers share one receive stream
        # (anyio hands each item to a single waiting receiver). The pool size bounds both
        # concurrency AND live-task count; backpressure is natural — idle consumers pull as
        # they finish while the backlog stays in the stream buffer. No limiter/semaphore needed.
        async with anyio.create_task_group() as tg:
            for _ in range(self._max_parallel):
                tg.start_soon(self._consume, receive_stream, handler)

    async def _consume(
        self,
        receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]],
        handler: _Handler,
    ) -> None:
        async for envelope in receive_stream:
            await self._paused.wait()
            try:
                await handler(envelope)
            except Exception:
                logger.exception(
                    'Unhandled error processing message_id=%s, worker continues',
                    envelope.message_id,
                )
