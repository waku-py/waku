from __future__ import annotations

import abc
import enum
from abc import ABC
from typing import TYPE_CHECKING

from waku.eventsourcing._introspection import is_abstract

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import ClassVar

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.checkpoint import Checkpoint

__all__ = [
    'ErrorPolicy',
    'ICatchUpProjection',
    'ICheckpointStore',
    'IProjection',
]


class IProjection(abc.ABC):
    projection_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if is_abstract(cls):
            return
        if not getattr(cls, 'projection_name', None):
            msg = f'{cls.__name__} must define projection_name class attribute'
            raise TypeError(msg)

    @abc.abstractmethod
    async def project(self, events: Sequence[StoredEvent], /) -> None: ...


@enum.unique
class ErrorPolicy(enum.StrEnum):
    SKIP = enum.auto()
    STOP = enum.auto()


class ICatchUpProjection(IProjection, ABC):
    """Projection that processes events asynchronously via polling.

    At-least-once delivery: the checkpoint is saved *after* ``project()`` processes
    a batch, so a crash before checkpoint save causes re-delivery on restart.
    ``project()`` must be idempotent.
    """

    async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
        pass

    async def teardown(self) -> None:
        pass


class ICheckpointStore(abc.ABC):
    @abc.abstractmethod
    async def load(self, projection_name: str, /) -> Checkpoint | None: ...

    @abc.abstractmethod
    async def save(self, checkpoint: Checkpoint, /) -> None: ...
