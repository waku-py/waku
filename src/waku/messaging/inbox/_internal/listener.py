from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from waku.messaging.inbox._internal.noop_backpressure import NoOpBackpressure
from waku.messaging.transport._internal.wire import rebuild_envelope
from waku.messaging.transport.inbound import ConsumeDisposition

if TYPE_CHECKING:
    from waku.messaging._internal.identity import MessageTypeRegistry
    from waku.messaging.endpoints._internal.durable_inbox_receiver import DurableInboxReceiver
    from waku.messaging.handler_map import HandlerMap
    from waku.messaging.inbox._internal.backpressure import IListenerBackpressure
    from waku.messaging.transport.interfaces import EnvelopeMetadata
    from waku.serialization.codec import PayloadCodec

__all__ = [
    'InboundListener',
]

logger = logging.getLogger(__name__)


class InboundListener:
    __slots__ = ('_backpressure', '_codec', '_receiver', '_registry', '_type_registry')

    def __init__(
        self,
        *,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        handler_map: HandlerMap,
        receiver: DurableInboxReceiver,
    ) -> None:
        self._codec = codec
        self._type_registry = type_registry
        self._registry = handler_map
        self._receiver = receiver
        self._backpressure: IListenerBackpressure = NoOpBackpressure()

    def attach_backpressure(self, backpressure: IListenerBackpressure) -> None:
        # Set by the wiring before the transport starts; consume() then reports the post-enqueue depth to it.
        self._backpressure = backpressure

    async def consume(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:
        try:
            envelope = rebuild_envelope(payload, metadata, self._codec, self._type_registry)
        except Exception:  # noqa: BLE001 -- poison (unknown type, bad UUID, missing timestamp) must be quarantined
            # Waku has no framework ping; an unresolvable message_type is foreign/poison — reject, do not requeue.
            logger.warning('Rejecting unrebuildable inbound message (unknown type or malformed metadata)')
            return ConsumeDisposition.REJECT
        handler_types = frozenset(self._registry.get_handler_types(type(envelope.payload)))
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
            # High-watermark check at the enqueue site: stop the listener when the in-memory backlog grows.
            await self._backpressure.observe_depth(self._receiver.queue_depth)
        return ConsumeDisposition.ACK
