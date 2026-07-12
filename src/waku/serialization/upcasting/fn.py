from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.serialization.upcasting.interfaces import IPayloadUpcaster

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['FnUpcaster']


class FnUpcaster(IPayloadUpcaster):
    """Function-backed upcaster comparing by value over ``(from_version, key)``.

    Helpers pass a structural ``key`` describing the transformation, so independently built but
    behaviorally identical helper chains compare equal. Without a key the wrapped function itself
    is the identity: distinct closures stay unequal.
    """

    __slots__ = ('_fn', '_key', 'from_version')

    def __init__(
        self,
        from_version: int,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        key: tuple[object, ...] | None = None,
    ) -> None:
        self.from_version = from_version
        self._fn = fn
        self._key: object = key if key is not None else fn

    def upcast(self, data: dict[str, Any], /) -> dict[str, Any]:
        return self._fn(data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FnUpcaster):
            return NotImplemented
        return (self.from_version, self._key) == (other.from_version, other._key)

    def __hash__(self) -> int:
        return hash((self.from_version, self._key))
