from __future__ import annotations

from waku.messages.message import IMessage

__all__ = [
    'IEvent',
]


class IEvent(IMessage):
    """Marker for event-type messages. Optional for messaging, required for event sourcing."""

    __slots__ = ()
