from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from waku.cqrs.events.map import EventMap
from waku.cqrs.pipeline.map import PipelineBehaviorMap
from waku.cqrs.requests.map import RequestMap
from waku.di import Provider, many, scoped

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['MediatorRegistry']


@dataclass(slots=True, kw_only=True)
class MediatorRegistry:
    request_map: RequestMap = field(default_factory=RequestMap)
    event_map: EventMap = field(default_factory=EventMap)
    behavior_map: PipelineBehaviorMap = field(default_factory=PipelineBehaviorMap)

    def merge(self, other: MediatorRegistry) -> None:
        self.request_map.merge(other.request_map)
        self.event_map.merge(other.event_map)
        self.behavior_map.merge(other.behavior_map)

    def freeze(self) -> None:
        self.request_map.freeze()
        self.event_map.freeze()
        self.behavior_map.freeze()

    def handler_providers(self) -> Iterator[Provider]:
        for entry in self.request_map.registry.values():
            yield scoped(entry.di_lookup_type, entry.handler_type)
        for entry in self.event_map.registry.values():
            yield many(entry.di_lookup_type, *entry.handler_types, collect=False)
        for entry in self.behavior_map.registry.values():
            yield many(entry.di_lookup_type, *entry.behavior_types, collect=False)

    def collector_providers(self) -> Iterator[Provider]:
        for entry in self.event_map.registry.values():
            yield many(entry.di_lookup_type, collect=True)
        for entry in self.behavior_map.registry.values():
            yield many(entry.di_lookup_type, collect=True)
