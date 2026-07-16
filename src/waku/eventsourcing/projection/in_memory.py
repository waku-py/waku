from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.eventsourcing.store.interfaces import ICheckpointStore

if TYPE_CHECKING:
    from waku.eventsourcing.projection.checkpoint import Checkpoint

__all__ = ['InMemoryCheckpointStore']


@dataclass
class InMemoryCheckpointState:
    """Mutable state backing one in-memory checkpoint store view."""

    checkpoints: dict[str, Checkpoint] = field(default_factory=dict)


class _InMemoryCheckpointStoreOperations(ICheckpointStore):
    __slots__ = ()

    def _get_state(self) -> InMemoryCheckpointState:
        msg = 'subclasses must provide checkpoint state'
        raise NotImplementedError(msg)

    @override
    async def load(self, projection_name: str, /) -> Checkpoint | None:
        return self._get_state().checkpoints.get(projection_name)

    @override
    async def save(self, checkpoint: Checkpoint, /) -> None:
        self._get_state().checkpoints[checkpoint.projection_name] = checkpoint


class InMemoryCheckpointStore(_InMemoryCheckpointStoreOperations):
    __slots__ = ('_state',)

    def __init__(self) -> None:
        self._state = InMemoryCheckpointState()

    @override
    def _get_state(self) -> InMemoryCheckpointState:
        return self._state
