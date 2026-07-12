from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

__all__ = ['LRUCache']

_T = TypeVar('_T')


class LRUCache(Generic[_T]):
    __slots__ = ('_cache', '_max_size')

    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, _T] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> _T | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, value: _T) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
