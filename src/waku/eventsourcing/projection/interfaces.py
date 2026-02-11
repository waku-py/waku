from __future__ import annotations

import abc
import inspect
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.checkpoint import Checkpoint

__all__ = [
    'ICheckpointStore',
    'IProjection',
]


class IProjection(abc.ABC):
    projection_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not getattr(cls, 'projection_name', None):
            msg = f'{cls.__name__} must define projection_name class attribute'
            raise TypeError(msg)

    @abc.abstractmethod
    async def project(self, events: Sequence[StoredEvent], /) -> None: ...


class ICheckpointStore(abc.ABC):
    @abc.abstractmethod
    async def load(self, projection_name: str, /) -> Checkpoint | None: ...

    @abc.abstractmethod
    async def save(self, checkpoint: Checkpoint, /) -> None: ...
