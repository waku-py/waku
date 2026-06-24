from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from waku.messaging.transport.inbound import ConsumeDisposition

if TYPE_CHECKING:
    from waku.messaging.endpoints.durable_inbox_receiver import DurableInboxReceiver
    from waku.messaging.inbox.backpressure import ListenerBackpressure
    from waku.messaging.registry import MessageRegistry
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
    'InboundListener',
]

logger = logging.getLogger(__name__)


class InboundListener:
    __slots__ = ('_backpressure', '_receiver', '_registry', '_serializer')

    def __init__(
        self,
        *,
        serializer: IEnvelopeSerializer,
        registry: MessageRegistry,
        receiver: DurableInboxReceiver,
    ) -> None:
        self._serializer = serializer
        self._registry = registry
        self._receiver = receiver
        self._backpressure: ListenerBackpressure | None = None

    def attach_backpressure(self, backpressure: ListenerBackpressure) -> None:
        # Set by the wiring before the transport starts; consume() then reports the post-enqueue depth to it.
        self._backpressure = backpressure

    async def consume(self, body: dict[str, Any]) -> ConsumeDisposition:
        try:
            envelope = self._serializer.deserialize(body)
        except Exception:  # noqa: BLE001 -- poison must be quarantined, not requeued
            logger.warning('Rejecting undeserializable inbound message')
            return ConsumeDisposition.REJECT
        handler_types = frozenset(self._registry.handler_map.get_handler_types(type(envelope.payload)))
        if not handler_types:
            logger.debug('No handler for inbound %s; acking', envelope.message_type)
            return ConsumeDisposition.ACK
        try:
            fresh = await self._receiver.persist(envelope, handler_types)
        except Exception:
            logger.exception('Inbound persist failed for message_id=%s; requeueing', envelope.message_id)
            return ConsumeDisposition.NACK_REQUEUE
        if fresh:
            await self._receiver.enqueue(envelope, fresh)
            if self._backpressure is not None:
                # High-watermark check at the enqueue site: stop the listener when the in-memory backlog grows.
                await self._backpressure.observe_depth(self._receiver.queue_depth)
        return ConsumeDisposition.ACK
