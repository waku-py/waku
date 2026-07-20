"""Shared inbound-dispatch skeleton for the FastStream broker transports.

The ``ConsumeDisposition`` -> ack/nack/reject decode/dispatch flow is identical across brokers; only the broker
disposition *primitives* differ (e.g. Kafka ``nack()`` seeks back, Rabbit ``nack(requeue=True)`` requeues). This base
owns the common flow and delegates every broker-specific call to the three abstract primitives, so it never touches
``msg`` directly. INTERNAL: not re-exported from a generic package — the two broker modules import it directly.
"""

from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import ITransport

if TYPE_CHECKING:
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import IEnvelopeMapper

__all__ = ['FastStreamTransportBase']

_BrokerMessageT = TypeVar('_BrokerMessageT')


class FastStreamTransportBase(ITransport, abc.ABC, Generic[_BrokerMessageT]):
    """Inbound-dispatch skeleton shared by the FastStream broker transports.

    Subclasses supply the broker disposition primitives (``_ack`` / ``_nack`` / ``_reject``); this base owns the
    decode-then-dispatch flow and never calls ``msg`` directly, so ``_BrokerMessageT`` needs no bound. ``map_incoming`` accepts
    ``_BrokerMessageT`` because the mapper is typed ``IEnvelopeMapper[Any, Any]`` at the dispatch call site.
    """

    async def _dispatch_inbound(
        self,
        msg: _BrokerMessageT,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any],
    ) -> None:
        # Logger bound to the concrete subclass's module so multi-broker deployments stay distinguishable in
        # logs (records read as ...faststream.kafka / ...faststream.rabbitmq, not this shared base module).
        log = logging.getLogger(type(self).__module__)
        try:
            payload, metadata = await mapper.map_incoming(msg)
        except Exception:
            log.exception('Undecodable or foreign inbound message rejected as poison')
            await self._reject(msg)
            return
        try:
            disposition = await on_message(payload, metadata)
        except Exception:
            log.exception('Inbound handler failed; message will be redelivered')
            await self._nack(msg)
            return
        if disposition is ConsumeDisposition.ACK:
            await self._ack(msg)
        elif disposition is ConsumeDisposition.NACK_REQUEUE:
            await self._nack(msg)
        else:
            await self._reject(msg)

    @abc.abstractmethod
    async def _ack(self, msg: _BrokerMessageT) -> None: ...

    @abc.abstractmethod
    async def _nack(self, msg: _BrokerMessageT) -> None: ...

    @abc.abstractmethod
    async def _reject(self, msg: _BrokerMessageT) -> None: ...
