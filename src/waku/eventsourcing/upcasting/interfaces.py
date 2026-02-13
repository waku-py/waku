from __future__ import annotations

import abc
from typing import Any

__all__ = ['IEventUpcaster']


class IEventUpcaster(abc.ABC):
    from_version: int

    @abc.abstractmethod
    def upcast(self, data: dict[str, Any], /) -> dict[str, Any]: ...
