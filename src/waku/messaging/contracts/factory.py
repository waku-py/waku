from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar
from uuid import UUID, uuid4

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.message import IMessage
from waku.messaging.identity import MessageTypeRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ['EnvelopeFactory']

_MessageT = TypeVar('_MessageT', bound=IMessage)


class EnvelopeFactory:
    __slots__ = ('_registry',)

    def __init__(self, registry: MessageTypeRegistry) -> None:
        self._registry = registry

    def create(
        self,
        message: _MessageT,
        *,
        message_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        headers: 'Mapping[str, str] | None' = None,
    ) -> 'MessageEnvelope[_MessageT]':
        message_id_ = message_id or uuid4()
        return MessageEnvelope(
            message_id=message_id_,
            correlation_id=correlation_id or uuid4(),
            causation_id=causation_id or message_id_,
            message_type=self._registry.resolve_name(type(message)),
            timestamp=datetime.now(tz=UTC),
            payload=message,
            headers=headers or {},
        )
