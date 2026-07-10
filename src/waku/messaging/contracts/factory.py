from typing import TYPE_CHECKING, TypeVar
from uuid import UUID, uuid4

from waku._internal.clock import Now, utc_now
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.message import IMessage
from waku.messaging.identity import MessageTypeRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = ['EnvelopeFactory']

_MessageT = TypeVar('_MessageT', bound=IMessage)


class EnvelopeFactory:
    __slots__ = ('_now', '_registry')

    def __init__(self, registry: MessageTypeRegistry, now: Now = utc_now) -> None:
        self._registry = registry
        self._now = now

    def create(  # noqa: PLR0913 -- envelope-native fields forwarded 1:1; bundling them is over-engineering for a factory
        self,
        message: _MessageT,
        *,
        message_id: UUID | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        headers: 'Mapping[str, str] | None' = None,
        group_id: str | None = None,
        scheduled_time: 'datetime | None' = None,
        expires_at: 'datetime | None' = None,
    ) -> 'MessageEnvelope[_MessageT]':
        message_id_ = message_id or uuid4()
        return MessageEnvelope(
            message_id=message_id_,
            correlation_id=correlation_id or str(uuid4()),
            causation_id=causation_id or str(message_id_),
            message_type=self._registry.resolve_name(type(message)),
            message_version=self._registry.resolve_version(type(message)),
            timestamp=self._now(),
            payload=message,
            headers=headers or {},
            group_id=group_id,
            scheduled_time=scheduled_time,
            expires_at=expires_at,
        )
