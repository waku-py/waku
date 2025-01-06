from __future__ import annotations

from collections import defaultdict
from typing import Any, Self, TypeAlias

from waku.ext.mediator.contracts.event import EventT
from waku.ext.mediator.events.handler import EventHandlerType
from waku.ext.mediator.exceptions import EventHandlerAlreadyRegistered, EventHandlerNotFound

__all__ = [
    'EventMap',
    'EventMapRegistry',
]

EventMapRegistry: TypeAlias = dict[type[EventT], list[EventHandlerType[EventT]]]


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

    def __getitem__(self, event_type: type[EventT]) -> list[EventHandlerType[EventT]]:
        try:
            return self._registry[event_type]
        except KeyError as err:
            msg = f'Event handlers for {event_type.__name__} event is not registered'
            raise EventHandlerNotFound(msg, event_type) from err

    def __bool__(self) -> bool:
        return bool(self._registry)