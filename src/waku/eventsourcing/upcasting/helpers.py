from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from waku.eventsourcing.upcasting.fn import FnUpcaster

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.eventsourcing.upcasting.interfaces import IEventUpcaster

__all__ = [
    'add_field',
    'noop',
    'remove_field',
    'rename_field',
    'upcast',
]


def noop(from_version: int) -> IEventUpcaster:
    return FnUpcaster(from_version, fn=dict)


def rename_field(from_version: int, *, old: str, new: str) -> IEventUpcaster:
    def _rename(data: dict[str, Any]) -> dict[str, Any]:
        result = {k: v for k, v in data.items() if k != old}
        if old in data:
            result[new] = data[old]
        return result

    return FnUpcaster(from_version, fn=_rename)


def add_field(from_version: int, *, field: str, default: Any) -> IEventUpcaster:
    def _add(data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        if field not in result:
            result[field] = copy.copy(default)
        return result

    return FnUpcaster(from_version, fn=_add)


def remove_field(from_version: int, *, field: str) -> IEventUpcaster:
    return FnUpcaster(from_version, fn=lambda data: {k: v for k, v in data.items() if k != field})


def upcast(from_version: int, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> IEventUpcaster:
    return FnUpcaster(from_version, fn=fn)
