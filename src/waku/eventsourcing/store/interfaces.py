from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.stream import StreamPosition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId

__all__ = [
    'IEventReader',
    'IEventStore',
    'IEventWriter',
]


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
    async def delete_stream(self, stream_id: StreamId, /) -> None:
        """Mark a stream as permanently deleted.

        Deleted streams are excluded from ``read_all``, ``read_positions``,
        and ``stream_exists``. Appending to a deleted stream raises
        ``StreamDeletedError``. Events remain accessible via ``read_stream``
        for audit purposes.

        Raises ``StreamNotFoundError`` if the stream does not exist.
        No-op if already deleted.
        """
        ...


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    pass
