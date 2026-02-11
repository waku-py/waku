from __future__ import annotations

from typing import Any

from waku.eventsourcing.exceptions import DuplicateEventTypeError, RegistryFrozenError, UnknownEventTypeError

__all__ = ['EventTypeRegistry']


class EventTypeRegistry:
    __slots__ = ('_frozen', '_name_to_type', '_type_to_name')

    def __init__(self) -> None:
        self._name_to_type: dict[str, type[Any]] = {}
        self._type_to_name: dict[type[Any], str] = {}
        self._frozen = False

    def register(self, event_type: type[Any], /, *, name: str | None = None) -> None:
        if self._frozen:
            raise RegistryFrozenError
        type_name = name or event_type.__name__
        if event_type in self._type_to_name:
            raise DuplicateEventTypeError(self._type_to_name[event_type])
        if type_name in self._name_to_type:
            raise DuplicateEventTypeError(type_name)
        self._name_to_type[type_name] = event_type
        self._type_to_name[event_type] = type_name

    def add_alias(self, event_type: type[Any], alias: str, /) -> None:
        if self._frozen:
            raise RegistryFrozenError
        if event_type not in self._type_to_name:
            raise UnknownEventTypeError(event_type.__name__)
        if alias in self._name_to_type:
            raise DuplicateEventTypeError(alias)
        self._name_to_type[alias] = event_type

    def resolve(self, event_type_name: str, /) -> type[Any]:
        try:
            return self._name_to_type[event_type_name]
        except KeyError:
            raise UnknownEventTypeError(event_type_name) from None

    def get_name(self, event_type: type[Any], /) -> str:
        try:
            return self._type_to_name[event_type]
        except KeyError:
            raise UnknownEventTypeError(event_type.__name__) from None

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def __contains__(self, event_type_name: str) -> bool:
        return event_type_name in self._name_to_type

    def __len__(self) -> int:
        return len(self._name_to_type)

    def merge(self, other: EventTypeRegistry) -> None:
        for event_type, type_name in other._type_to_name.items():
            self.register(event_type, name=type_name)
