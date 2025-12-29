from __future__ import annotations

from collections import defaultdict
from collections.abc import MutableMapping
from typing import Any, Self, TypeAlias

from waku.cqrs.contracts.notification import INotification, NotificationT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.exceptions import EventHandlerAlreadyRegistered

__all__ = [
    'EventMap',
    'EventMapRegistry',
]

EventMapRegistry: TypeAlias = MutableMapping[type[INotification], list[type[EventHandler[Any]]]]


class EventMap:
    def __init__(self) -> None:
        self._registry: EventMapRegistry = defaultdict(list)

    def bind(self, event_type: type[NotificationT], handler_types: list[type[EventHandler[NotificationT]]]) -> Self:
        for handler_type in handler_types:
            if handler_type in self._registry[event_type]:
                raise EventHandlerAlreadyRegistered(event_type, handler_type)
            self._registry[event_type].append(handler_type)
        return self

    def merge(self, other: EventMap) -> Self:
        for event_type, handlers in other.registry.items():
            self.bind(event_type, handlers)
        return self

    @property
    def registry(self) -> EventMapRegistry:
        return self._registry

    def __bool__(self) -> bool:
        return bool(self._registry)
