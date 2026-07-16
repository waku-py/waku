from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.eventsourcing.store.interfaces import ICheckpointStore

if TYPE_CHECKING:
    from waku.eventsourcing.projection.checkpoint import Checkpoint

__all__ = ['InMemoryCheckpointStore']


class InMemoryCheckpointStore(ICheckpointStore):
    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}

    @override
    async def load(self, projection_name: str, /) -> Checkpoint | None:
        return self._checkpoints.get(projection_name)

    @override
    async def save(self, checkpoint: Checkpoint, /) -> None:
        self._checkpoints[checkpoint.projection_name] = checkpoint
