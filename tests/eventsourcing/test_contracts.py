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
    DuplicateAggregateNameError,
    DuplicateIdempotencyKeyError,
    EventSourcingError,
    ProjectionError,
    ProjectionStoppedError,
    StreamNotFoundError,
)
from waku.exceptions import WakuError


def test_stream_id_stores_value() -> None:
    stream_id = StreamId(stream_type='order', stream_key='123')
    assert stream_id.value == 'order-123'


def test_stream_id_exposes_fields() -> None:
    stream_id = StreamId(stream_type='order', stream_key='abc-456')
    assert stream_id.stream_type == 'order'
    assert stream_id.stream_key == 'abc-456'


def test_stream_id_for_aggregate_formats_type_and_id() -> None:
    stream_id = StreamId.for_aggregate('Order', 'abc-456')
    assert stream_id.value == 'Order-abc-456'
    assert stream_id.stream_type == 'Order'
    assert stream_id.stream_key == 'abc-456'


def test_stream_id_str_returns_value() -> None:
    stream_id = StreamId(stream_type='user', stream_key='789')
    assert str(stream_id) == 'user-789'


def test_stream_id_from_value_roundtrip() -> None:
    original = StreamId.for_aggregate('Order', 'abc-456')
    parsed = StreamId.from_value(str(original))
    assert parsed == original


def test_stream_id_from_value_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match='Invalid stream ID format'):
        StreamId.from_value('nodash')

    with pytest.raises(ValueError, match='Invalid stream ID format'):
        StreamId.from_value('')


def test_stream_id_empty_stream_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match='StreamId stream_type cannot be empty'):
        StreamId(stream_type='', stream_key='123')


def test_stream_id_hyphen_in_stream_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match='must not contain hyphens'):
        StreamId(stream_type='bank-account', stream_key='123')


def test_stream_id_empty_stream_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match='StreamId stream_key cannot be empty'):
        StreamId(stream_type='order', stream_key='')


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


def test_event_metadata_defaults() -> None:
    meta = EventMetadata()
    assert meta.correlation_id is None
    assert meta.causation_id is None
    assert meta.extra == {}


def test_event_envelope_defaults() -> None:
    envelope = EventEnvelope(domain_event='SomeEvent', idempotency_key='test-key')
    assert envelope.domain_event == 'SomeEvent'
    assert envelope.idempotency_key == 'test-key'
    assert envelope.metadata == EventMetadata()


def test_event_envelope_empty_idempotency_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match='idempotency_key must not be empty'):
        EventEnvelope(domain_event='SomeEvent', idempotency_key='')


def test_stored_event_construction() -> None:
    event_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    meta = EventMetadata(correlation_id='corr-1', causation_id='cause-1')
    stored = StoredEvent(
        event_id=event_id,
        stream_id=StreamId(stream_type='order', stream_key='1'),
        event_type='OrderCreated',
        position=0,
        global_position=42,
        timestamp=now,
        data={'total': 100},
        metadata=meta,
        idempotency_key='test-key',
    )
    assert stored.event_id == event_id
    assert stored.stream_id == StreamId(stream_type='order', stream_key='1')
    assert stored.event_type == 'OrderCreated'
    assert stored.position == 0
    assert stored.global_position == 42
    assert stored.timestamp == now
    assert stored.data == {'total': 100}
    assert stored.metadata.correlation_id == 'corr-1'


def test_event_sourcing_error_is_waku_error_subclass() -> None:
    assert issubclass(EventSourcingError, WakuError)


def test_stream_not_found_error_carries_stream_id() -> None:
    stream_id = StreamId.for_aggregate('order', '1')
    error = StreamNotFoundError(stream_id=stream_id)
    assert error.stream_id == stream_id
    assert 'order-1' in str(error)
    assert isinstance(error, EventSourcingError)


def test_concurrency_conflict_error_carries_attrs() -> None:
    stream_id = StreamId.for_aggregate('order', '1')
    error = ConcurrencyConflictError(stream_id=stream_id, expected_version=3, actual_version=5)
    assert error.stream_id == stream_id
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


def test_projection_error_hierarchy() -> None:
    assert issubclass(ProjectionError, EventSourcingError)
    assert issubclass(ProjectionError, WakuError)
    assert issubclass(ProjectionStoppedError, ProjectionError)


def test_projection_stopped_error_carries_attrs() -> None:
    cause = RuntimeError('boom')
    error = ProjectionStoppedError(projection_name='search_index', cause=cause)
    assert error.projection_name == 'search_index'
    assert error.cause is cause
    assert 'search_index' in str(error)
    assert 'boom' in str(error)


def test_duplicate_idempotency_key_error_within_batch() -> None:
    stream_id = StreamId.for_aggregate('order', '1')
    error = DuplicateIdempotencyKeyError(stream_id, reason='duplicate keys within batch')
    assert error.stream_id == stream_id
    assert error.reason == 'duplicate keys within batch'
    assert 'duplicate keys within batch' in str(error)
    assert 'order-1' in str(error)
    assert isinstance(error, EventSourcingError)


def test_duplicate_idempotency_key_error_conflict_with_existing() -> None:
    stream_id = StreamId.for_aggregate('order', '1')
    error = DuplicateIdempotencyKeyError(stream_id, reason='conflict with existing keys')
    assert error.stream_id == stream_id
    assert error.reason == 'conflict with existing keys'
    assert 'conflict with existing keys' in str(error)
    assert 'order-1' in str(error)
    assert isinstance(error, EventSourcingError)


def test_duplicate_aggregate_name_error_carries_attrs() -> None:
    class RepoA: ...

    class RepoB: ...

    error = DuplicateAggregateNameError('Order', [RepoA, RepoB])
    assert error.aggregate_name == 'Order'
    assert error.repositories == [RepoA, RepoB]
    assert 'Order' in str(error)
    assert 'RepoA' in str(error)
    assert 'RepoB' in str(error)
    assert isinstance(error, EventSourcingError)
