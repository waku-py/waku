from __future__ import annotations

import abc
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from typing_extensions import override

from waku.messages import MessageIdentity
from waku.messaging.contracts.envelope import MessageEnvelope

if TYPE_CHECKING:
    # dishka introspects the factory's signature, not this __init__, so these stay TYPE_CHECKING-only.
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
            'scheduled_time': envelope.scheduled_time.isoformat() if envelope.scheduled_time is not None else None,
            'expires_at': envelope.expires_at.isoformat() if envelope.expires_at is not None else None,
            'payload': self._codec.encode(envelope.payload, type(envelope.payload)),
        }

    @override
    def deserialize(self, data: dict[str, Any]) -> MessageEnvelope[Any]:
        message_type_name = data['message_type']
        message_version = data.get('message_version', 1)
        payload_type = self._type_registry.resolve_type(message_type_name)
        identity = MessageIdentity(name=message_type_name, version=message_version)
        payload: Any = self._codec.decode(data['payload'], payload_type, identity)
        scheduled_raw = data.get('scheduled_time')
        expires_raw = data.get('expires_at')
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
            scheduled_time=datetime.fromisoformat(scheduled_raw).astimezone(UTC) if scheduled_raw is not None else None,
            expires_at=datetime.fromisoformat(expires_raw).astimezone(UTC) if expires_raw is not None else None,
        )
