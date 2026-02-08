from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Generic, Self, TypeAlias

from waku.cqrs.contracts.pipeline import IPipelineBehavior
from waku.cqrs.contracts.request import IRequest, RequestT, ResponseT
from waku.cqrs.exceptions import PipelineBehaviorAlreadyRegistered
from waku.cqrs.utils import get_request_response_type

__all__ = [
    'PipelineBehaviorMap',
    'PipelineBehaviorMapEntry',
    'PipelineBehaviorMapRegistry',
]


@dataclass(frozen=True, slots=True)
class PipelineBehaviorMapEntry(Generic[RequestT, ResponseT]):
    request_type: type[IRequest[ResponseT]]
    di_lookup_type: type[IPipelineBehavior[RequestT, ResponseT]]
    behavior_types: list[type[IPipelineBehavior[RequestT, ResponseT]]] = field(default_factory=list)

    @classmethod
    def for_request(cls, request_type: type[IRequest[ResponseT]]) -> Self:
        response_type = get_request_response_type(request_type)
        di_lookup_type = IPipelineBehavior[request_type, response_type]  # type: ignore[valid-type]
        return cls(request_type=request_type, di_lookup_type=di_lookup_type)  # type: ignore[type-abstract]

    def add(self, behavior_type: type[IPipelineBehavior[RequestT, ResponseT]]) -> None:
        if behavior_type in self.behavior_types:
            raise PipelineBehaviorAlreadyRegistered(self.request_type, behavior_type)
        self.behavior_types.append(behavior_type)


PipelineBehaviorMapRegistry: TypeAlias = MutableMapping[type[RequestT], PipelineBehaviorMapEntry[RequestT, ResponseT]]


class PipelineBehaviorMap:
    def __init__(self) -> None:
        self._registry: PipelineBehaviorMapRegistry[Any, Any] = {}

    def bind(
        self,
        request_type: type[RequestT],
        behavior_types: list[type[IPipelineBehavior[RequestT, ResponseT]]],
    ) -> Self:
        if request_type not in self._registry:
            self._registry[request_type] = PipelineBehaviorMapEntry.for_request(request_type)

        entry = self._registry[request_type]
        for behavior_type in behavior_types:
            entry.add(behavior_type)
        return self

    def merge(self, other: PipelineBehaviorMap) -> Self:
        for request_type, entry in other._registry.items():
            self.bind(request_type, entry.behavior_types)
        return self

    @property
    def registry(self) -> PipelineBehaviorMapRegistry[Any, Any]:
        return self._registry

    def has_behaviors(self, request_type: type[RequestT]) -> bool:
        return request_type in self._registry and len(self._registry[request_type].behavior_types) > 0

    def get_lookup_type(self, request_type: type[RequestT]) -> type[IPipelineBehavior[Any, Any]]:
        return self._registry[request_type].di_lookup_type

    def __bool__(self) -> bool:
        return bool(self._registry)
