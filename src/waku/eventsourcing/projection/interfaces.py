from __future__ import annotations

import abc
import enum
from abc import ABC
from typing import TYPE_CHECKING

from waku.eventsourcing._internal.introspection import is_abstract

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import ClassVar

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.messages import IEvent

__all__ = [
    'ICatchUpProjection',
    'IProjection',
    'ProjectionErrorPolicy',
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
class ProjectionErrorPolicy(enum.StrEnum):
    SKIP = enum.auto()
    STOP = enum.auto()


class ICatchUpProjection(IProjection, ABC):
    """Projection that processes events asynchronously via polling.

    At-least-once delivery: the checkpoint is saved *after* ``project()`` processes
    a batch, so a crash before checkpoint save causes re-delivery on restart.
    ``project()`` must be idempotent.

    Set ``event_types`` to filter which event types this projection receives.
    ``None`` (default) delivers every event. A non-empty sequence delivers exactly those types,
    alias-expanded to include historical names. An empty sequence is rejected with
    ``EventSourcingConfigError`` at module registration.
    """

    event_types: ClassVar[Sequence[type[IEvent]] | None] = None

    async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
        pass

    async def teardown(self) -> None:
        pass
