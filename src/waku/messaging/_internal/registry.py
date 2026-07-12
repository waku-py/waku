from __future__ import annotations

from dataclasses import dataclass, field

from waku.messaging._internal.handler_map import HandlerMap

__all__ = ['MessageRegistry']


@dataclass(slots=True, kw_only=True)
class MessageRegistry:
    handler_map: HandlerMap = field(default_factory=HandlerMap)

    def merge(self, other: MessageRegistry) -> None:
        self.handler_map.merge(other.handler_map)

    def freeze(self) -> None:
        self.handler_map.freeze()
