from __future__ import annotations

from collections.abc import Iterator, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Self, TypeAlias

from waku.messaging._introspection import get_response_type
from waku.messaging.contracts.message import IMessage, MessageT, ResponseT
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.exceptions import MapFrozenError, PipelineBehaviorAlreadyRegistered

__all__ = [
    'PipelineBehaviorMap',
    'PipelineBehaviorMapEntry',
]


@dataclass(slots=True)
class PipelineBehaviorMapEntry(Generic[MessageT, ResponseT]):
    message_type: type[IMessage]
    di_lookup_type: type[IPipelineBehavior[MessageT, ResponseT]]
    behavior_types: list[type[IPipelineBehavior[Any, Any]]] = field(default_factory=list)

    @classmethod
    def for_message(cls, message_type: type[IMessage]) -> Self:
        response_type = get_response_type(message_type)
        di_lookup_type = IPipelineBehavior[message_type, response_type]  # type: ignore[valid-type]
        return cls(message_type=message_type, di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

    def add(self, behavior_type: type[IPipelineBehavior[Any, Any]]) -> None:
        if behavior_type in self.behavior_types:
            raise PipelineBehaviorAlreadyRegistered(self.message_type, behavior_type)
        self.behavior_types.append(behavior_type)


PipelineBehaviorMapRegistry: TypeAlias = MutableMapping[type[MessageT], PipelineBehaviorMapEntry[MessageT, ResponseT]]


class PipelineBehaviorMap:
    def __init__(self) -> None:
        self._registry: PipelineBehaviorMapRegistry[Any, Any] = {}
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def bind(
        self,
        entry: PipelineBehaviorMapEntry[Any, Any],
        behavior_types: Sequence[type[IPipelineBehavior[Any, Any]]],
    ) -> Self:
        if self._frozen:
            raise MapFrozenError
        if entry.message_type not in self._registry:
            self._registry[entry.message_type] = entry

        existing = self._registry[entry.message_type]
        for behavior_type in behavior_types:
            existing.add(behavior_type)
        return self

    def merge(self, other: PipelineBehaviorMap) -> Self:
        if self._frozen:
            raise MapFrozenError
        for other_entry in other.entries():
            if other_entry.message_type not in self._registry:
                self._registry[other_entry.message_type] = PipelineBehaviorMapEntry(
                    message_type=other_entry.message_type,
                    di_lookup_type=other_entry.di_lookup_type,
                )
            target = self._registry[other_entry.message_type]
            for behavior_type in other_entry.behavior_types:
                target.add(behavior_type)
        return self

    def entries(self) -> Iterator[PipelineBehaviorMapEntry[Any, Any]]:
        yield from self._registry.values()

    def has_behaviors(self, message_type: type[IMessage]) -> bool:
        return message_type in self._registry and len(self._registry[message_type].behavior_types) > 0

    def get_behavior_types(self, message_type: type[IMessage]) -> Sequence[type[IPipelineBehavior[Any, Any]]]:
        return self._registry[message_type].behavior_types

    def get_lookup_type(self, message_type: type[IMessage]) -> type[IPipelineBehavior[Any, Any]]:
        return self._registry[message_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
