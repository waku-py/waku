from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

_T = TypeVar('_T')


class LRUCache(Generic[_T]):
    """LRU cache for module type data with controlled size."""

    __slots__ = ('_cache', '_max_size')

    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, _T] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> _T | None:
        """Get value from cache, moving it to end if found."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, value: _T) -> None:
        """Add/update value in cache, removing the oldest if at capacity."""
        self._cache[key] = value
        self._cache.move_to_end(key)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

    def __len__(self) -> int:
        """Return the number of items in the cache."""
        return len(self._cache)
