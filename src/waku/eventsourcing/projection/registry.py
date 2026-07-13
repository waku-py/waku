from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import DuplicateProjectionNameError, UnknownProjectionError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection

__all__ = ['CatchUpProjectionRegistry']


class CatchUpProjectionRegistry:
    def __init__(self, bindings: tuple[CatchUpProjectionBinding, ...]) -> None:
        self._bindings = bindings
        by_name: dict[str, CatchUpProjectionBinding] = {}
        for b in self._bindings:
            name = b.projection.projection_name
            if name in by_name:
                raise DuplicateProjectionNameError(name)
            by_name[name] = b
        self._by_name = by_name

    def __iter__(self) -> Iterator[CatchUpProjectionBinding]:
        return iter(self._bindings)

    def __len__(self) -> int:
        return len(self._bindings)

    def get(self, projection_name: str) -> CatchUpProjectionBinding:
        try:
            return self._by_name[projection_name]
        except KeyError:
            raise UnknownProjectionError(projection_name) from None

    def subset(self, projections: Sequence[type[ICatchUpProjection]]) -> CatchUpProjectionRegistry:
        wanted = set(projections)
        return CatchUpProjectionRegistry(tuple(b for b in self._bindings if b.projection in wanted))
