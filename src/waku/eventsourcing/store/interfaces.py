from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.stream import StreamPosition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId
    from waku.eventsourcing.projection.checkpoint import Checkpoint
    from waku.eventsourcing.snapshot.interfaces import Snapshot

__all__ = [
    'ICheckpointStore',
    'IEventReader',
    'IEventStore',
    'IEventWriter',
    'ISnapshotStore',
]


class ISnapshotStore(abc.ABC):
    @abc.abstractmethod
    async def load(self, stream_id: StreamId, /) -> Snapshot | None: ...

    @abc.abstractmethod
    async def save(self, snapshot: Snapshot, /) -> None: ...


class ICheckpointStore(abc.ABC):
    @abc.abstractmethod
    async def load(self, projection_name: str, /) -> Checkpoint | None: ...

    @abc.abstractmethod
    async def save(self, checkpoint: Checkpoint, /) -> None: ...


class IEventReader(abc.ABC):
    @abc.abstractmethod
    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]: ...

    @abc.abstractmethod
    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> list[StoredEvent]: ...

    @abc.abstractmethod
    async def stream_exists(self, stream_id: StreamId, /) -> bool: ...

    @abc.abstractmethod
    async def global_head_position(self) -> int:
        """Return the highest global position in the store, or ``-1`` if empty."""
        ...

    @abc.abstractmethod
    async def read_positions(
        self,
        *,
        after_position: int,
        up_to_position: int,
    ) -> list[int]:
        """Return committed global positions in the range ``(after_position, up_to_position]``.

        Positions are returned in ascending order.
        """
        ...


class IEventWriter(abc.ABC):
    @abc.abstractmethod
    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int: ...

    @abc.abstractmethod
    async def archive_stream(self, stream_id: StreamId, /) -> None:
        """Mark a stream as archived.

        Archived streams are excluded from ``read_all``, ``read_positions``,
        and ``stream_exists``; their events remain readable via ``read_stream``
        for audit purposes. Appending to an archived stream raises
        ``StreamArchivedError`` — parity with Marten, whose ``mt_quick_append_events``
        also raises on an archived stream. The ``stream_exists`` exclusion is a
        Waku choice stricter than Marten's archive semantics.

        Raises ``StreamNotFoundError`` if the stream does not exist.
        No-op if already archived.
        """
        ...

    @property
    def records_appended_events(self) -> bool:
        """Whether this store records appended domain events into ``IAppendedEvents`` for forwarding.

        Default ``False``: a store forwards nothing unless it deliberately wires the appended-events
        collector, so ES startup validation rejects ``forwarding=[...]`` against a store whose trait is
        ``False``. Recording stores override to ``True``.
        """
        return False


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    """Cohesive per-backend event-sourcing store: append/read stay primary; snapshots/checkpoints are facets.

    A backend assembles the facets over its single scoped resource, so a facet port resolved from the
    same scope IS the corresponding facet of this object. Projection LOCKS are deliberately excluded —
    coordination, not durability.
    """

    @property
    @abc.abstractmethod
    def snapshots(self) -> ISnapshotStore: ...

    @property
    @abc.abstractmethod
    def checkpoints(self) -> ICheckpointStore: ...
