from __future__ import annotations

from uuid import uuid4

from waku._internal.node import NodeId
from waku.messaging.inbox import EndpointUri, HandlerDestination
from waku.messaging.inbox.models import InboxEntry

SAMPLE_OWNER = NodeId('node-a')


def make_sample_inbox_entry() -> InboxEntry:
    return InboxEntry(
        id=uuid4(),
        owner_id=SAMPLE_OWNER,
        payload={'test': True},
        message_type='test.Event',
        source_uri=EndpointUri('local://orders'),
        destination=HandlerDestination('tests.messaging.HandlerA'),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
    )
