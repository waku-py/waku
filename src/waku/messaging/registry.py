from __future__ import annotations

from dataclasses import dataclass, field

from waku.messaging.handler_map import HandlerMap
from waku.messaging.pipeline.map import PipelineBehaviorMap

__all__ = ['MessageRegistry']


@dataclass(slots=True, kw_only=True)
class MessageRegistry:
    handler_map: HandlerMap = field(default_factory=HandlerMap)
    behavior_map: PipelineBehaviorMap = field(default_factory=PipelineBehaviorMap)

    def merge(self, other: MessageRegistry) -> None:
        self.handler_map.merge(other.handler_map)
        self.behavior_map.merge(other.behavior_map)

    def freeze(self) -> None:
        self.handler_map.freeze()
        self.behavior_map.freeze()
