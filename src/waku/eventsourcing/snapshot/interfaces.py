from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import StreamId

__all__ = [
    'ISnapshotStore',
    'ISnapshotStrategy',
    'Snapshot',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    stream_id: StreamId
    state: dict[str, Any]
    version: int
    state_type: str


class ISnapshotStore(abc.ABC):
    @abc.abstractmethod
    async def load(self, stream_id: StreamId, /) -> Snapshot | None: ...

    @abc.abstractmethod
    async def save(self, snapshot: Snapshot, /) -> None: ...


class ISnapshotStrategy(abc.ABC):
    @abc.abstractmethod
    def should_snapshot(self, version: int, events_since_snapshot: int) -> bool: ...
