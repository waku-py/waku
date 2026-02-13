from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.eventsourcing.upcasting.interfaces import IEventUpcaster

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['FnUpcaster']


class FnUpcaster(IEventUpcaster):
    __slots__ = ('_fn', 'from_version')

    def __init__(self, from_version: int, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.from_version = from_version
        self._fn = fn

    def upcast(self, data: dict[str, Any], /) -> dict[str, Any]:
        return self._fn(data)
