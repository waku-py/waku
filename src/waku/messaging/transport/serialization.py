from __future__ import annotations

import abc
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from typing_extensions import override

from waku.messages import MessageIdentity
from waku.messaging.contracts.envelope import MessageEnvelope

if TYPE_CHECKING:
    # Constructed only via the _create_envelope_serializer factory — dishka introspects that
    # factory's signature, never this __init__ — so these deps stay TYPE_CHECKING-only.
    from waku.messaging.identity import MessageTypeRegistry
    from waku.serialization.codec import PayloadCodec

__all__ = [
    'IEnvelopeSerializer',
    'JsonEnvelopeSerializer',
]


class IEnvelopeSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, envelope: MessageEnvelope[Any]) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any]) -> MessageEnvelope[Any]: ...


class JsonEnvelopeSerializer(IEnvelopeSerializer):
    __slots__ = ('_codec', '_type_registry')

    def __init__(self, type_registry: MessageTypeRegistry, codec: PayloadCodec) -> None:
        self._type_registry = type_registry
        self._codec = codec

    @override
    def serialize(self, envelope: MessageEnvelope[Any]) -> dict[str, Any]:
        return {
            'message_id': str(envelope.message_id),
            'correlation_id': str(envelope.correlation_id),
            'causation_id': str(envelope.causation_id),
            'message_type': envelope.message_type,
            'message_version': envelope.message_version,
            'timestamp': envelope.timestamp.isoformat(),
            'headers': dict(envelope.headers),
            'group_id': envelope.group_id,
            'payload': self._codec.encode(envelope.payload, type(envelope.payload)),
        }

    @override
    def deserialize(self, data: dict[str, Any]) -> MessageEnvelope[Any]:
        message_type_name = data['message_type']
        message_version = data.get('message_version', 1)
        payload_type = self._type_registry.resolve_type(message_type_name)
        identity = MessageIdentity(name=message_type_name, version=message_version)
        payload: Any = self._codec.decode(data['payload'], payload_type, identity)
        return MessageEnvelope(
            message_id=UUID(data['message_id']),
            correlation_id=UUID(data['correlation_id']),
            causation_id=UUID(data['causation_id']),
            message_type=message_type_name,
            message_version=message_version,
            timestamp=datetime.fromisoformat(data['timestamp']).astimezone(UTC),
            payload=payload,
            headers=data.get('headers', {}),
            group_id=data.get('group_id'),
        )
