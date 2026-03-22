from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any

import anyio
from anyio import create_memory_object_stream

from waku.messaging.context import MessageContext, reset_message_context, set_message_context
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint, HandlerSubscriptions

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
    from dishka import AsyncContainer

logger = logging.getLogger(__name__)


class LocalQueueEndpoint(Endpoint):
    __slots__ = ('_container', '_receive_stream', '_send_stream', '_stop_timeout', '_worker_task')

    def __init__(
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        container: AsyncContainer,
        stop_timeout: float,
    ) -> None:
        super().__init__(uri=uri, handler_subscriptions=handler_subscriptions)
        self._container = container
        self._stop_timeout = stop_timeout
        send, receive = create_memory_object_stream[MessageEnvelope[Any]](max_buffer_size=math.inf)
        self._send_stream: MemoryObjectSendStream[MessageEnvelope[Any]] = send
        self._receive_stream: MemoryObjectReceiveStream[MessageEnvelope[Any]] = receive
        self._worker_task: asyncio.Task[None] | None = None

    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:  # noqa: ARG002
        await self._send_stream.send(envelope)

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
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
                await self._process_envelope(envelope)

    async def _process_envelope(self, envelope: MessageEnvelope[Any]) -> None:
        try:
            async with self._container() as scope:
                dispatcher = await scope.get(MessageDispatcher)
                ctx = MessageContext(
                    correlation_id=envelope.correlation_id,
                    causation_id=envelope.causation_id,
                    message_id=envelope.message_id,
                    headers=envelope.headers,
                )
                token = set_message_context(ctx)
                try:
                    await self._execute(dispatcher, envelope)
                finally:
                    reset_message_context(token)
        except Exception:
            logger.exception(
                'Failed to process message: message_id=%s, message_type=%s',
                envelope.message_id,
                envelope.message_type,
            )

    async def _execute(self, dispatcher: MessageDispatcher, envelope: MessageEnvelope[Any]) -> None:
        payload = envelope.payload
        handler_types = self._handler_subscriptions.get(type(payload))
        if handler_types is not None:
            await dispatcher.publish_event_only(payload, only=handler_types)
        else:
            await dispatcher.invoke_request(payload)
