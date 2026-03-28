from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from adaptix import Retort, dumper, loader
from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'IEnvelopeSerializer',
    'JsonEnvelopeSerializer',
]

_retort = Retort(
    recipe=[
        loader(UUID, UUID),
        dumper(UUID, str),
    ],
)


@runtime_checkable
class IEnvelopeSerializer(Protocol):
    def serialize(self, envelope: MessageEnvelope[Any]) -> bytes: ...
    def deserialize(self, data: bytes) -> MessageEnvelope[Any]: ...


class JsonEnvelopeSerializer(IEnvelopeSerializer):
    @override
    def serialize(self, envelope: MessageEnvelope[Any]) -> bytes:
        payload = _retort.dump(envelope.payload, type(envelope.payload))
        doc = {
            'message_id': str(envelope.message_id),
            'correlation_id': str(envelope.correlation_id),
            'causation_id': str(envelope.causation_id),
            'message_type': envelope.message_type,
            'timestamp': envelope.timestamp.isoformat(),
            'headers': dict(envelope.headers),
            'payload': payload,
        }
        return json.dumps(doc, separators=(',', ':')).encode()

    @override
    def deserialize(self, data: bytes) -> MessageEnvelope[Any]:
        doc = json.loads(data)
        payload_type = self._resolve_type(doc['message_type'])
        payload: Any = _retort.load(doc['payload'], payload_type)
        return MessageEnvelope(
            message_id=UUID(doc['message_id']),
            correlation_id=UUID(doc['correlation_id']),
            causation_id=UUID(doc['causation_id']),
            message_type=doc['message_type'],
            timestamp=datetime.fromisoformat(doc['timestamp']).astimezone(UTC),
            payload=payload,
            headers=doc.get('headers', {}),
        )

    @staticmethod
    def _resolve_type(message_type: str) -> type:
        module_path, _, class_name = message_type.rpartition('.')
        module = importlib.import_module(module_path)
        return getattr(module, class_name)  # type: ignore[no-any-return]
