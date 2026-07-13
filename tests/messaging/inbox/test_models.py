from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from waku.messaging._internal.identifiers import EndpointUri, HandlerDestination
from waku.messaging.inbox.models import InboxEntry, InboxStatus


def _make_entry(**overrides: Any) -> InboxEntry:
    kwargs: dict[str, Any] = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'source_uri': EndpointUri('local://orders'),
        'destination': HandlerDestination('tests.messaging.HandlerA'),
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
    }
    kwargs.update(overrides)
    return InboxEntry(**kwargs)


def test_inbox_entry_metadata_field_is_metadata() -> None:
    entry = _make_entry(metadata={'k': 'v'})
    assert entry.metadata == {'k': 'v'}
    assert not hasattr(entry, 'metadata_')


def test_scheduled_entry_without_execution_time_is_rejected() -> None:
    with pytest.raises(ValueError, match='execution_time'):
        _make_entry(status=InboxStatus.SCHEDULED, execution_time=None)


def test_scheduled_entry_with_execution_time_constructs() -> None:
    entry = _make_entry(status=InboxStatus.SCHEDULED, execution_time=datetime.now(tz=UTC))
    assert entry.status is InboxStatus.SCHEDULED


def test_incoming_entry_needs_no_execution_time() -> None:
    entry = _make_entry(status=InboxStatus.INCOMING, execution_time=None)
    assert entry.status is InboxStatus.INCOMING
