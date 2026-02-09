from __future__ import annotations

from typing import Any

from waku.eventsourcing.exceptions import DuplicateEventTypeError, RegistryFrozenError, UnknownEventTypeError

__all__ = ['EventTypeRegistry']


class EventTypeRegistry:
    __slots__ = ('_frozen', '_types')

    def __init__(self) -> None:
        self._types: dict[str, type[Any]] = {}
        self._frozen = False

    def register(self, event_type: type[Any], /, *, name: str | None = None) -> None:
        if self._frozen:
            raise RegistryFrozenError
        type_name = name or event_type.__qualname__
        if type_name in self._types:
            raise DuplicateEventTypeError(type_name)
        self._types[type_name] = event_type

    def resolve(self, event_type_name: str, /) -> type[Any]:
        try:
            return self._types[event_type_name]
        except KeyError:
            raise UnknownEventTypeError(event_type_name) from None

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def __contains__(self, event_type_name: str) -> bool:
        return event_type_name in self._types

    def __len__(self) -> int:
        return len(self._types)

    def merge(self, other: EventTypeRegistry) -> None:
        for type_name, event_type in other._types.items():
            if type_name in self._types:
                raise DuplicateEventTypeError(type_name)
            self._types[type_name] = event_type
