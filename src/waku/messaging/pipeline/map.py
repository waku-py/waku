from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Self, TypeAlias

from waku.messaging._introspection import get_request_response_type
from waku.messaging.contracts.message import IMessage, MessageT, ResponseT
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.exceptions import MapFrozenError, PipelineBehaviorAlreadyRegistered

if TYPE_CHECKING:
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.request import IRequest

__all__ = [
    'PipelineBehaviorMap',
    'PipelineBehaviorMapEntry',
    'PipelineBehaviorMapRegistry',
]


@dataclass(slots=True)
class PipelineBehaviorMapEntry(Generic[MessageT, ResponseT]):
    message_type: type[IMessage]
    di_lookup_type: type[IPipelineBehavior[MessageT, ResponseT]]
    behavior_types: list[type[IPipelineBehavior[Any, Any]]] = field(default_factory=list)

    @classmethod
    def for_request(cls, request_type: type[IRequest[ResponseT]]) -> Self:
        response_type = get_request_response_type(request_type)
        di_lookup_type = IPipelineBehavior[request_type, response_type]  # type: ignore[valid-type]
        return cls(message_type=request_type, di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

    @classmethod
    def for_event(cls, event_type: type[IEvent]) -> Self:
        di_lookup_type = IPipelineBehavior[event_type, None]  # type: ignore[valid-type]
        return cls(message_type=event_type, di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

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
        behavior_types: list[type[IPipelineBehavior[Any, Any]]],
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
        for other_entry in other._registry.values():
            if other_entry.message_type not in self._registry:
                self._registry[other_entry.message_type] = PipelineBehaviorMapEntry(
                    message_type=other_entry.message_type,
                    di_lookup_type=other_entry.di_lookup_type,
                )
            target = self._registry[other_entry.message_type]
            for behavior_type in other_entry.behavior_types:
                target.add(behavior_type)
        return self

    @property
    def registry(self) -> PipelineBehaviorMapRegistry[Any, Any]:
        return self._registry

    def has_behaviors(self, message_type: type[Any]) -> bool:
        return message_type in self._registry and len(self._registry[message_type].behavior_types) > 0

    def get_lookup_type(self, message_type: type[Any]) -> type[IPipelineBehavior[Any, Any]]:
        return self._registry[message_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
