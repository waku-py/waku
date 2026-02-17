from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import StreamId


@dataclass(frozen=True)
class SomeEvent:
    value: str


def test_stored_event_schema_version_defaults_to_one() -> None:
    event = StoredEvent(
        event_id=uuid.uuid4(),
        stream_id=StreamId(stream_type='stream', stream_key='1'),
        event_type='SomeEvent',
        position=0,
        global_position=0,
        timestamp=datetime.now(UTC),
        data=SomeEvent(value='x'),
        metadata=EventMetadata(),
        idempotency_key='test-key',
    )
    assert event.schema_version == 1


def test_stored_event_schema_version_can_be_set() -> None:
    event = StoredEvent(
        event_id=uuid.uuid4(),
        stream_id=StreamId(stream_type='stream', stream_key='1'),
        event_type='SomeEvent',
        position=0,
        global_position=0,
        timestamp=datetime.now(UTC),
        data=SomeEvent(value='x'),
        metadata=EventMetadata(),
        idempotency_key='test-key',
        schema_version=3,
    )
    assert event.schema_version == 3
