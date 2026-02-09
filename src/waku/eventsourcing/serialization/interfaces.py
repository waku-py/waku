from __future__ import annotations

import abc
from typing import Any

__all__ = ['IEventSerializer']


class IEventSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, event: Any, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> Any: ...
