from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TypeVar

EventT = TypeVar('EventT', bound='Event', contravariant=True)  # noqa: PLC0105


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base class for events."""

    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
