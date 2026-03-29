from __future__ import annotations

import abc
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from typing_extensions import override

from waku._internal.retort import default_retort
from waku.messaging.contracts.envelope import MessageEnvelope

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    __slots__ = ('_type_registry',)

    def __init__(self, type_registry: Mapping[str, type]) -> None:
        self._type_registry = type_registry

    @override
    def serialize(self, envelope: MessageEnvelope[Any]) -> dict[str, Any]:
        return {
            'message_id': str(envelope.message_id),
            'correlation_id': str(envelope.correlation_id),
            'causation_id': str(envelope.causation_id),
            'message_type': envelope.message_type,
            'timestamp': envelope.timestamp.isoformat(),
            'headers': dict(envelope.headers),
            'payload': default_retort.dump(envelope.payload, type(envelope.payload)),
        }

    @override
    def deserialize(self, data: dict[str, Any]) -> MessageEnvelope[Any]:
        message_type_name = data['message_type']
        payload_type = self._resolve_type(message_type_name)
        payload: Any = default_retort.load(data['payload'], payload_type)
        return MessageEnvelope(
            message_id=UUID(data['message_id']),
            correlation_id=UUID(data['correlation_id']),
            causation_id=UUID(data['causation_id']),
            message_type=message_type_name,
            timestamp=datetime.fromisoformat(data['timestamp']).astimezone(UTC),
            payload=payload,
            headers=data.get('headers', {}),
        )

    def _resolve_type(self, message_type: str) -> type:
        try:
            return self._type_registry[message_type]
        except KeyError:
            registered = sorted(self._type_registry)
            msg = f"Unknown message type '{message_type}'. Registered types: {registered}"
            raise ValueError(msg) from None
