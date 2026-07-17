from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['LRUCache']

_ValueT = TypeVar('_ValueT')


class LRUCache(Generic[_ValueT]):
    __slots__ = ('_cache', '_max_size')

    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, _ValueT] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> _ValueT | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, value: _ValueT) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def get_or_compute(self, key: str, compute: Callable[[], _ValueT]) -> _ValueT:
        cached = self.get(key)
        if cached is not None:
            return cached
        result = compute()
        self.put(key, result)
        return result

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
