from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

import anyio
from anyio import create_memory_object_stream
from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.endpoints.base import Endpoint

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from waku.di import AsyncContainer
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.router import HandlerSubscriptions

logger = logging.getLogger(__name__)


class LocalQueueEndpoint(Endpoint):
    __slots__ = (
        '_executor',
        '_handler_subscriptions',
        '_max_buffer_size',
        '_receive_stream',
        '_send_stream',
        '_stop_timeout',
        '_worker_task',
    )

    def __init__(
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: EndpointExecutor,
        stop_timeout: float,
        max_buffer_size: float,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._stop_timeout = stop_timeout
        self._max_buffer_size = max_buffer_size
        self._send_stream: MemoryObjectSendStream[MessageEnvelope[Any]] | None = None
        self._receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]] | None = None
        self._worker_task: asyncio.Task[None] | None = None

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        send_stream = self._send_stream
        if send_stream is None:
            logger.warning('Message dropped: endpoint %s is stopped (message_id=%s)', self._uri, envelope.message_id)
            return
        await send_stream.send(envelope)

    async def start(self) -> None:
        send, receive = create_memory_object_stream[MessageEnvelope[Any]](max_buffer_size=self._max_buffer_size)
        self._send_stream = send
        self._receive_stream = receive
        self._worker_task = asyncio.create_task(self._worker_loop(receive))

    async def stop(self) -> None:
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
        try:
            with anyio.fail_after(self._stop_timeout):
                await task
        except TimeoutError:
            logger.warning(
                'Worker task for %s did not terminate within %.1fs, cancelling',
                self._uri,
                self._stop_timeout,
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _worker_loop(self, receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]]) -> None:
        async for envelope in receive_stream:
            try:
                await self._process_envelope(envelope)
            except Exception:
                logger.exception(
                    'Unhandled error processing message_id=%s, continuing worker loop',
                    envelope.message_id,
                )

    async def _process_envelope(self, envelope: MessageEnvelope[Any]) -> None:
        for handler_type in self._handler_subscriptions.get(type(envelope.payload), ()):
            await self._executor.execute(envelope, handler_type)
