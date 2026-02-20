from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from waku.eventsourcing._stream_helpers import read_aggregate_stream  # noqa: PLC2701
from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError, StreamTooLargeError
from waku.eventsourcing.store.interfaces import IEventStore


@pytest.fixture
def event_store() -> AsyncMock:
    return AsyncMock(spec=IEventStore)


@pytest.fixture
def stream_id() -> StreamId:
    return StreamId.for_aggregate('TestAggregate', 'agg-1')


async def test_returns_stored_events(event_store: AsyncMock, stream_id: StreamId) -> None:
    sentinel = [AsyncMock(spec=StoredEvent)]
    event_store.read_stream.return_value = sentinel

    result = await read_aggregate_stream(
        event_store,
        stream_id,
        aggregate_name='TestAggregate',
        aggregate_id='agg-1',
        max_stream_length=None,
    )

    assert result is sentinel


async def test_raises_aggregate_not_found_on_stream_not_found(
    event_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    event_store.read_stream.side_effect = StreamNotFoundError(stream_id)

    with pytest.raises(AggregateNotFoundError) as exc_info:
        await read_aggregate_stream(
            event_store,
            stream_id,
            aggregate_name='TestAggregate',
            aggregate_id='agg-1',
            max_stream_length=None,
        )

    assert exc_info.value.aggregate_type == 'TestAggregate'
    assert exc_info.value.aggregate_id == 'agg-1'


async def test_raises_stream_too_large_when_exceeding_max_length(
    event_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    event_store.read_stream.return_value = [AsyncMock()] * 4

    with pytest.raises(StreamTooLargeError) as exc_info:
        await read_aggregate_stream(
            event_store,
            stream_id,
            aggregate_name='TestAggregate',
            aggregate_id='agg-1',
            max_stream_length=3,
        )

    assert exc_info.value.stream_id == stream_id
    assert exc_info.value.max_length == 3


async def test_passes_max_length_plus_one_as_count(
    event_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    event_store.read_stream.return_value = []

    await read_aggregate_stream(
        event_store,
        stream_id,
        aggregate_name='TestAggregate',
        aggregate_id='agg-1',
        max_stream_length=5,
    )

    event_store.read_stream.assert_called_once_with(stream_id, count=6)


async def test_passes_none_count_when_no_max_length(
    event_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    event_store.read_stream.return_value = []

    await read_aggregate_stream(
        event_store,
        stream_id,
        aggregate_name='TestAggregate',
        aggregate_id='agg-1',
        max_stream_length=None,
    )

    event_store.read_stream.assert_called_once_with(stream_id, count=None)


async def test_succeeds_at_exactly_max_length(
    event_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    event_store.read_stream.return_value = [AsyncMock()] * 3

    result = await read_aggregate_stream(
        event_store,
        stream_id,
        aggregate_name='TestAggregate',
        aggregate_id='agg-1',
        max_stream_length=3,
    )

    assert len(result) == 3
