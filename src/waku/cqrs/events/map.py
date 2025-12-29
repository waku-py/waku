from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Generic, Self, TypeAlias

from typing_extensions import TypeVar

from waku.cqrs.contracts.notification import INotification, NotificationT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.exceptions import EventHandlerAlreadyRegistered

__all__ = [
    'EventMap',
    'EventMapEntry',
    'EventMapRegistry',
]

_EventT = TypeVar('_EventT', bound=INotification, default=INotification)


@dataclass(frozen=True, slots=True)
class EventMapEntry(Generic[_EventT]):
    di_lookup_type: type[EventHandler[_EventT]]
    handler_types: list[type[EventHandler[_EventT]]] = field(default_factory=list)


EventMapRegistry: TypeAlias = MutableMapping[type[INotification], EventMapEntry[INotification]]


class EventMap:
    def __init__(self) -> None:
        self._registry: EventMapRegistry = {}

    def bind(self, event_type: type[NotificationT], handler_types: list[type[EventHandler[NotificationT]]]) -> Self:
        if event_type not in self._registry:
            di_lookup_type = EventHandler[event_type]  # type: ignore[valid-type]
            self._registry[event_type] = EventMapEntry(di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

        entry = self._registry[event_type]
        for handler_type in handler_types:
            if handler_type in entry.handler_types:
                raise EventHandlerAlreadyRegistered(event_type, handler_type)
            entry.handler_types.append(handler_type)  # type: ignore[arg-type]
        return self

    def merge(self, other: EventMap) -> Self:
        for event_type, entry in other._registry.items():
            self.bind(event_type, entry.handler_types)
        return self

    @property
    def registry(self) -> EventMapRegistry:
        return self._registry

    def has_handlers(self, event_type: type[INotification]) -> bool:
        return event_type in self._registry and len(self._registry[event_type].handler_types) > 0

    def get_handler_type(self, event_type: type[INotification]) -> type[EventHandler[INotification]]:
        return self._registry[event_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
