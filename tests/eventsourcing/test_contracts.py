from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from waku.eventsourcing.contracts import (
    AnyVersion,
    EventEnvelope,
    EventMetadata,
    Exact,
    NoStream,
    StoredEvent,
    StreamExists,
    StreamId,
)
from waku.eventsourcing.exceptions import (
    AggregateNotFoundError,
    ConcurrencyConflictError,
    EventSourcingError,
    StreamNotFoundError,
)
from waku.exceptions import WakuError

# --- StreamId ---


def test_stream_id_stores_value() -> None:
    stream_id = StreamId(value='order-123')
    assert stream_id.value == 'order-123'


def test_stream_id_for_aggregate_formats_type_and_id() -> None:
    stream_id = StreamId.for_aggregate('Order', 'abc-456')
    assert stream_id.value == 'Order-abc-456'


def test_stream_id_str_returns_value() -> None:
    stream_id = StreamId(value='user-789')
    assert str(stream_id) == 'user-789'


def test_stream_id_empty_value_raises_value_error() -> None:
    with pytest.raises(ValueError, match='StreamId cannot be empty'):
        StreamId(value='')


# --- ExpectedVersion ADT ---


def test_exact_stores_version() -> None:
    version = Exact(version=5)
    assert version.version == 5


def test_no_stream_stream_exists_any_version_are_equal_by_value() -> None:
    assert NoStream() == NoStream()
    assert StreamExists() == StreamExists()
    assert AnyVersion() == AnyVersion()


def test_exact_is_frozen() -> None:
    exact = Exact(version=1)
    with pytest.raises(FrozenInstanceError):
        exact.version = 99  # type: ignore[misc]


# --- EventMetadata ---


def test_event_metadata_defaults() -> None:
    meta = EventMetadata()
    assert meta.correlation_id is None
    assert meta.causation_id is None
    assert meta.extra == {}


# --- EventEnvelope ---


def test_event_envelope_defaults() -> None:
    envelope = EventEnvelope(domain_event='SomeEvent')
    assert envelope.domain_event == 'SomeEvent'
    assert envelope.metadata == EventMetadata()


# --- StoredEvent ---


def test_stored_event_construction() -> None:
    event_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    meta = EventMetadata(correlation_id='corr-1', causation_id='cause-1')
    stored = StoredEvent(
        event_id=event_id,
        stream_id='order-1',
        event_type='OrderCreated',
        position=0,
        global_position=42,
        timestamp=now,
        data={'total': 100},
        metadata=meta,
    )
    assert stored.event_id == event_id
    assert stored.stream_id == 'order-1'
    assert stored.event_type == 'OrderCreated'
    assert stored.position == 0
    assert stored.global_position == 42
    assert stored.timestamp == now
    assert stored.data == {'total': 100}
    assert stored.metadata.correlation_id == 'corr-1'


# --- Exceptions ---


def test_event_sourcing_error_is_waku_error_subclass() -> None:
    assert issubclass(EventSourcingError, WakuError)


def test_stream_not_found_error_carries_stream_id() -> None:
    error = StreamNotFoundError(stream_id='order-1')
    assert error.stream_id == 'order-1'
    assert 'order-1' in str(error)
    assert isinstance(error, EventSourcingError)


def test_concurrency_conflict_error_carries_attrs() -> None:
    error = ConcurrencyConflictError(stream_id='order-1', expected_version=3, actual_version=5)
    assert error.stream_id == 'order-1'
    assert error.expected_version == 3
    assert error.actual_version == 5
    assert 'order-1' in str(error)
    assert isinstance(error, EventSourcingError)


def test_aggregate_not_found_error_carries_attrs() -> None:
    error = AggregateNotFoundError(aggregate_type='Order', aggregate_id='abc-123')
    assert error.aggregate_type == 'Order'
    assert error.aggregate_id == 'abc-123'
    assert 'Order' in str(error)
    assert 'abc-123' in str(error)
    assert isinstance(error, EventSourcingError)
