from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from uuid import UUID

T = TypeVar('T')


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageEnvelope(Generic[T]):
    message_id: UUID
    correlation_id: UUID
    causation_id: UUID
    message_type: str
    timestamp: datetime
    payload: T
    headers: Mapping[str, str] = field(default_factory=dict)
