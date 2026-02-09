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
    ) -> list[StoredEvent]: ...

    @abc.abstractmethod
    async def stream_exists(self, stream_id: StreamId, /) -> bool: ...


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


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    pass
