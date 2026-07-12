from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from waku.serialization.upcasting.fn import FnUpcaster

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.serialization.upcasting.interfaces import IPayloadUpcaster

__all__ = [
    'add_field',
    'noop',
    'remove_field',
    'rename_field',
    'upcast',
]


def noop(from_version: int) -> IPayloadUpcaster:
    return FnUpcaster(from_version, fn=dict, key=('noop',))


def rename_field(from_version: int, *, old: str, new: str) -> IPayloadUpcaster:
    def _rename(data: dict[str, Any]) -> dict[str, Any]:
        result = {k: v for k, v in data.items() if k != old}
        if old in data:
            result[new] = data[old]
        return result

    return FnUpcaster(from_version, fn=_rename, key=('rename_field', old, new))


def add_field(from_version: int, *, field: str, default: Any) -> IPayloadUpcaster:
    def _add(data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        if field not in result:
            result[field] = copy.copy(default)
        return result

    return FnUpcaster(from_version, fn=_add, key=('add_field', field, _hashable_or_repr(default)))


def remove_field(from_version: int, *, field: str) -> IPayloadUpcaster:
    return FnUpcaster(
        from_version,
        fn=lambda data: {k: v for k, v in data.items() if k != field},
        key=('remove_field', field),
    )


def upcast(from_version: int, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> IPayloadUpcaster:
    return FnUpcaster(from_version, fn=fn)


def _hashable_or_repr(default: Any) -> object:
    try:
        hash(default)
    except TypeError:
        return repr(default)
    return default
