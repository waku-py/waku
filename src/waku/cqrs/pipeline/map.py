from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Self, TypeAlias

from waku.cqrs.contracts.pipeline import IPipelineBehavior
from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.exceptions import PipelineBehaviorAlreadyRegistered
from waku.cqrs.utils import get_request_response_type

__all__ = [
    'PipelineBehaviorMap',
    'PipelineBehaviorMapEntry',
    'PipelineBehaviorMapRegistry',
]


@dataclass(frozen=True, slots=True)
class PipelineBehaviorMapEntry:
    di_lookup_type: type[IPipelineBehavior[Any, Any]]
    behavior_types: list[type[IPipelineBehavior[Any, Any]]] = field(default_factory=list)


PipelineBehaviorMapRegistry: TypeAlias = MutableMapping[type[RequestT], PipelineBehaviorMapEntry]  # ty: ignore[invalid-argument-type]


class PipelineBehaviorMap:
    def __init__(self) -> None:
        self._registry: PipelineBehaviorMapRegistry[Any] = {}

    def bind(
        self,
        request_type: type[RequestT],
        behavior_types: list[type[IPipelineBehavior[RequestT, ResponseT]]],
    ) -> Self:
        if request_type not in self._registry:
            response_type = get_request_response_type(request_type)
            di_lookup_type = IPipelineBehavior[request_type, response_type]  # type: ignore[valid-type]
            self._registry[request_type] = PipelineBehaviorMapEntry(di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

        entry = self._registry[request_type]
        for behavior_type in behavior_types:
            if behavior_type in entry.behavior_types:
                raise PipelineBehaviorAlreadyRegistered(request_type, behavior_type)
            entry.behavior_types.append(behavior_type)
        return self

    def merge(self, other: PipelineBehaviorMap) -> Self:
        for request_type, entry in other._registry.items():
            self.bind(request_type, entry.behavior_types)
        return self

    @property
    def registry(self) -> PipelineBehaviorMapRegistry[Any]:
        return self._registry

    def has_behaviors(self, request_type: type[RequestT]) -> bool:
        return request_type in self._registry and len(self._registry[request_type].behavior_types) > 0

    def get_lookup_type(self, request_type: type[RequestT]) -> type[IPipelineBehavior[Any, Any]]:
        return self._registry[request_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
