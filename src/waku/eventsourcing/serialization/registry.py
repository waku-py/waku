from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import (
    ConflictingEventTypeError,
    DuplicateEventTypeError,
    RegistryFrozenError,
    UnknownEventTypeError,
)
from waku.messages import MessageIdentity

if TYPE_CHECKING:
    from waku.messages import IEvent

__all__ = ['EventTypeRegistry']


class EventTypeRegistry:
    __slots__ = ('_frozen', '_name_to_type', '_type_to_identity')

    def __init__(self) -> None:
        self._name_to_type: dict[str, type[IEvent]] = {}
        self._type_to_identity: dict[type[IEvent], MessageIdentity] = {}
        self._frozen = False

    def register(self, event_type: type[IEvent], /, *, name: str | None = None, version: int = 1) -> None:
        if self._frozen:
            raise RegistryFrozenError
        type_name = name or event_type.__name__

        if event_type in self._type_to_identity:
            existing = self._type_to_identity[event_type]
            if existing.name == type_name and existing.version == version:
                return
            raise ConflictingEventTypeError(event_type.__name__, existing.name, existing.version, type_name, version)

        if type_name in self._name_to_type:
            raise DuplicateEventTypeError(type_name)

        self._name_to_type[type_name] = event_type
        self._type_to_identity[event_type] = MessageIdentity(name=type_name, version=version)

    def add_alias(self, event_type: type[IEvent], alias: str, /) -> None:
        if self._frozen:
            raise RegistryFrozenError
        if event_type not in self._type_to_identity:
            raise UnknownEventTypeError(event_type.__name__)
        if alias in self._name_to_type:
            if self._name_to_type[alias] is event_type:
                return
            raise DuplicateEventTypeError(alias)
        self._name_to_type[alias] = event_type

    def resolve(self, event_type_name: str, /) -> type[IEvent]:
        try:
            return self._name_to_type[event_type_name]
        except KeyError:
            raise UnknownEventTypeError(event_type_name) from None

    def get_name(self, event_type: type[IEvent], /) -> str:
        return self.get_identity(event_type).name

    def get_identity(self, event_type: type[IEvent], /) -> MessageIdentity:
        try:
            return self._type_to_identity[event_type]
        except KeyError:
            raise UnknownEventTypeError(event_type.__name__) from None

    def get_version(self, event_type: type[IEvent], /) -> int:
        return self.get_identity(event_type).version

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def __contains__(self, event_type_name: str) -> bool:
        return event_type_name in self._name_to_type

    def __len__(self) -> int:
        return len(self._name_to_type)
