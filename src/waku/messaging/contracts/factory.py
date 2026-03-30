from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.message import IMessage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

_MessageT = TypeVar('_MessageT', bound=IMessage)


class EnvelopeFactory:
    @classmethod
    def create(
        cls,
        message: _MessageT,
        *,
        message_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> MessageEnvelope[_MessageT]:
        message_id_ = message_id or uuid4()
        return MessageEnvelope(
            message_id=message_id_,
            correlation_id=correlation_id or uuid4(),
            causation_id=causation_id or message_id_,
            message_type=f'{type(message).__module__}.{type(message).__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=message,
            headers=headers or {},
        )
