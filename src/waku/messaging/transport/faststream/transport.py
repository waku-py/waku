from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging.transport.interfaces import ITransport

if TYPE_CHECKING:
    from faststream._internal.broker.broker import BrokerUsecase as BrokerType

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
    'FastStreamTransport',
]


class FastStreamTransport(ITransport):
    __slots__ = ('_broker', '_serializer')

    def __init__(self, broker: BrokerType[Any, Any], serializer: IEnvelopeSerializer) -> None:
        self._broker = broker
        self._serializer = serializer

    @override
    async def send(self, envelope: MessageEnvelope[Any]) -> None:
        message_payload = self._serializer.serialize(envelope)
        destination = envelope.headers.get('destination', envelope.message_type)
        await self._broker.publish(  # type: ignore[call-arg]
            message_payload,
            destination,
            headers={  # pyrefly: ignore[unexpected-keyword]
                'message_id': str(envelope.message_id),
                'correlation_id': str(envelope.correlation_id),
                'causation_id': str(envelope.causation_id),
                'message_type': envelope.message_type,
            },
        )

    @override
    async def publish(self, envelope: MessageEnvelope[Any]) -> None:
        await self.send(envelope)
