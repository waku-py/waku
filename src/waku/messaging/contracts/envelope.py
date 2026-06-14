from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from waku.messaging.contracts.message import IMessage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from uuid import UUID

_MessageT = TypeVar('_MessageT', bound=IMessage)


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageEnvelope(Generic[_MessageT]):
    message_id: UUID
    correlation_id: UUID
    causation_id: UUID
    message_type: str
    timestamp: datetime
    payload: _MessageT
    headers: Mapping[str, str] = field(default_factory=dict)
    group_id: str | None = None
