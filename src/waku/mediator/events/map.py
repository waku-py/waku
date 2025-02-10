from __future__ import annotations

from collections import defaultdict
from collections.abc import MutableMapping
from typing import Any, Self, TypeAlias

from waku.mediator.contracts.event import EventT
from waku.mediator.events.handler import EventHandlerType
from waku.mediator.exceptions import EventHandlerAlreadyRegistered

__all__ = [
    'EventMap',
    'EventMapRegistry',
]

EventMapRegistry: TypeAlias = MutableMapping[type[EventT], list[EventHandlerType[EventT]]]


class EventMap:
    def __init__(self) -> None:
        self._registry: EventMapRegistry[Any] = defaultdict(list)

    def bind(self, event_type: type[EventT], handler_types: list[EventHandlerType[EventT]]) -> Self:
        for handler_type in handler_types:
            if handler_type in self._registry[event_type]:
                msg = f'{handler_type.__name__} already registered for {event_type.__name__} event'
                raise EventHandlerAlreadyRegistered(msg, event_type, handler_type)
            self._registry[event_type].append(handler_type)
        return self

    def merge(self, other: EventMap) -> Self:
        for event_type, handlers in other.registry.items():
            self.bind(event_type, handlers)
        return self

    @property
    def registry(self) -> EventMapRegistry[Any]:
        return self._registry

    def __bool__(self) -> bool:
        return bool(self._registry)
