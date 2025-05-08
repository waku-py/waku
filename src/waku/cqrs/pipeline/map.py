from __future__ import annotations

from collections import defaultdict
from collections.abc import MutableMapping
from typing import Any, Self

from waku.cqrs.contracts.pipeline import IPipelineBehavior
from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.exceptions import PipelineBehaviorAlreadyRegistered

PipelineBehaviorMapRegistry = MutableMapping[type[RequestT], list[type[IPipelineBehavior[RequestT, ResponseT]]]]


class PipelineBehaviourMap:
    def __init__(self) -> None:
        self._registry: PipelineBehaviorMapRegistry[Any, Any] = defaultdict(list)

    def bind(
        self,
        request_type: type[RequestT],
        behavior_types: list[type[IPipelineBehavior[RequestT, ResponseT]]],
    ) -> Self:
        for behavior_type in behavior_types:
            if behavior_type in self._registry[request_type]:
                raise PipelineBehaviorAlreadyRegistered(request_type, behavior_type)
            self._registry[request_type].append(behavior_type)
        return self

    def merge(self, other: PipelineBehaviourMap) -> Self:
        for event_type, handlers in other.registry.items():
            self.bind(event_type, handlers)
        return self

    @property
    def registry(self) -> PipelineBehaviorMapRegistry[Any, Any]:
        return self._registry

    def __bool__(self) -> bool:
        return bool(self._registry)
