from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding

__all__ = ['CatchUpProjectionRegistry']


class CatchUpProjectionRegistry:
    def __init__(self, bindings: tuple[CatchUpProjectionBinding, ...]) -> None:
        self._bindings = bindings
        self._by_name: dict[str, CatchUpProjectionBinding] = {b.projection.projection_name: b for b in self._bindings}

    def __iter__(self) -> Iterator[CatchUpProjectionBinding]:
        return iter(self._bindings)

    def __len__(self) -> int:
        return len(self._bindings)

    def get(self, projection_name: str) -> CatchUpProjectionBinding:
        try:
            return self._by_name[projection_name]
        except KeyError:
            msg = f'Projection {projection_name!r} not found'
            raise ValueError(msg) from None
