from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent


@dataclass(frozen=True)
class SomeEvent:
    value: str


def test_stored_event_schema_version_defaults_to_one() -> None:
    event = StoredEvent(
        event_id=uuid.uuid4(),
        stream_id='stream-1',
        event_type='SomeEvent',
        position=0,
        global_position=0,
        timestamp=datetime.now(UTC),
        data=SomeEvent(value='x'),
        metadata=EventMetadata(),
    )
    assert event.schema_version == 1


def test_stored_event_schema_version_can_be_set() -> None:
    event = StoredEvent(
        event_id=uuid.uuid4(),
        stream_id='stream-1',
        event_type='SomeEvent',
        position=0,
        global_position=0,
        timestamp=datetime.now(UTC),
        data=SomeEvent(value='x'),
        metadata=EventMetadata(),
        schema_version=3,
    )
    assert event.schema_version == 3
