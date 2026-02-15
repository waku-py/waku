from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import (
    ConflictingEventTypeError,
    DuplicateEventTypeError,
    RegistryFrozenError,
    UnknownEventTypeError,
)

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification

__all__ = ['EventTypeRegistry']


class EventTypeRegistry:
    __slots__ = ('_frozen', '_name_to_type', '_type_to_name', '_type_to_version')

    def __init__(self) -> None:
        self._name_to_type: dict[str, type[INotification]] = {}
        self._type_to_name: dict[type[INotification], str] = {}
        self._type_to_version: dict[type[INotification], int] = {}
        self._frozen = False

    def register(self, event_type: type[INotification], /, *, name: str | None = None, version: int = 1) -> None:
        if self._frozen:
            raise RegistryFrozenError
        type_name = name or event_type.__name__

        if event_type in self._type_to_name:
            existing_name = self._type_to_name[event_type]
            existing_version = self._type_to_version[event_type]
            if existing_name == type_name and existing_version == version:
                return
            raise ConflictingEventTypeError(event_type, existing_name, existing_version, type_name, version)

        if type_name in self._name_to_type:
            raise DuplicateEventTypeError(type_name)

        self._name_to_type[type_name] = event_type
        self._type_to_name[event_type] = type_name
        self._type_to_version[event_type] = version

    def add_alias(self, event_type: type[INotification], alias: str, /) -> None:
        if self._frozen:
            raise RegistryFrozenError
        if event_type not in self._type_to_name:
            raise UnknownEventTypeError(event_type.__name__)
        if alias in self._name_to_type:
            if self._name_to_type[alias] is event_type:
                return
            raise DuplicateEventTypeError(alias)
        self._name_to_type[alias] = event_type

    def resolve(self, event_type_name: str, /) -> type[INotification]:
        try:
            return self._name_to_type[event_type_name]
        except KeyError:
            raise UnknownEventTypeError(event_type_name) from None

    def get_name(self, event_type: type[INotification], /) -> str:
        try:
            return self._type_to_name[event_type]
        except KeyError:
            raise UnknownEventTypeError(event_type.__name__) from None

    def get_version(self, event_type: type[INotification], /) -> int:
        try:
            return self._type_to_version[event_type]
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
