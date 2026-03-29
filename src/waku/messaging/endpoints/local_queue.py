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
    from dishka import AsyncContainer

    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.router import HandlerSubscriptions

logger = logging.getLogger(__name__)


class LocalQueueEndpoint(Endpoint):
    __slots__ = (
        '_executor',
        '_handler_subscriptions',
        '_receive_stream',
        '_send_stream',
        '_stop_timeout',
        '_stopped',
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
        send, receive = create_memory_object_stream[MessageEnvelope[Any]](max_buffer_size=max_buffer_size)
        self._send_stream: MemoryObjectSendStream[MessageEnvelope[Any]] = send
        self._receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]] = receive
        self._worker_task: asyncio.Task[None] | None = None
        self._stopped = False

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        if self._stopped:
            logger.warning('Message dropped: endpoint %s is stopped (message_id=%s)', self._uri, envelope.message_id)
            return
        await self._send_stream.send(envelope)

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._stopped = True
        self._send_stream.close()
        if self._worker_task is None:
            return
        try:
            with anyio.fail_after(self._stop_timeout):
                await self._worker_task
        except TimeoutError:
            logger.warning(
                'Worker task for %s did not terminate within %.1fs, cancelling',
                self._uri,
                self._stop_timeout,
            )
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _worker_loop(self) -> None:
        async with self._receive_stream:
            async for envelope in self._receive_stream:
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
