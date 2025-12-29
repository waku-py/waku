from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from waku.cqrs.contracts.notification import INotification, NotificationT

__all__ = [
    'Event',
    'INotification',
    'NotificationT',
]


@dataclass(frozen=True, kw_only=True)
class Event(INotification):
    """Convenience base class for events with auto-generated ID.

    Use this class when you want automatic event_id generation.
    For custom identification strategies, implement INotification directly.

    Example::

        @dataclass(frozen=True, kw_only=True)
        class UserCreated(Event):
            user_id: str
            email: str

    """

    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
