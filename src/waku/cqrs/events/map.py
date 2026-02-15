from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Generic, Self, TypeAlias

from typing_extensions import TypeVar

from waku.cqrs.contracts.notification import INotification, NotificationT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.exceptions import EventHandlerAlreadyRegistered, MapFrozenError

__all__ = [
    'EventMap',
    'EventMapEntry',
    'EventMapRegistry',
]

_EventT = TypeVar('_EventT', bound=INotification, default=INotification)


@dataclass(frozen=True, slots=True)
class EventMapEntry(Generic[_EventT]):
    event_type: type[INotification]
    di_lookup_type: type[EventHandler[_EventT]]
    handler_types: list[type[EventHandler[_EventT]]] = field(default_factory=list)

    @classmethod
    def for_event(cls, event_type: type[INotification]) -> Self:
        di_lookup_type = EventHandler[event_type]  # type: ignore[valid-type]
        return cls(event_type=event_type, di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

    def add(self, handler_type: type[EventHandler[_EventT]]) -> None:
        if handler_type in self.handler_types:
            raise EventHandlerAlreadyRegistered(self.event_type, handler_type)
        self.handler_types.append(handler_type)


EventMapRegistry: TypeAlias = MutableMapping[type[INotification], EventMapEntry[INotification]]


class EventMap:
    def __init__(self) -> None:
        self._registry: EventMapRegistry = {}
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def bind(self, event_type: type[NotificationT], handler_types: list[type[EventHandler[NotificationT]]]) -> Self:
        if self._frozen:
            raise MapFrozenError
        if event_type not in self._registry:
            self._registry[event_type] = EventMapEntry.for_event(event_type)

        entry = self._registry[event_type]
        for handler_type in handler_types:
            entry.add(handler_type)  # type: ignore[arg-type]
        return self

    def merge(self, other: EventMap) -> Self:
        if self._frozen:
            raise MapFrozenError
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
