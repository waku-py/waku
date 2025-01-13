from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

EventT = TypeVar('EventT', bound='Event', contravariant=True)  # noqa: PLC0105


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base class for events."""
