from __future__ import annotations

from uuid import uuid4

from waku.messaging._internal.identifiers import EndpointUri, HandlerDestination
from waku.messaging.inbox.models import InboxEntry


def test_inbox_entry_metadata_field_is_metadata() -> None:
    entry = InboxEntry(
        id=uuid4(),
        payload={'test': True},
        message_type='test.Event',
        source_uri=EndpointUri('local://orders'),
        destination=HandlerDestination('tests.messaging.HandlerA'),
        metadata={'k': 'v'},
    )
    assert entry.metadata == {'k': 'v'}
    assert not hasattr(entry, 'metadata_')
