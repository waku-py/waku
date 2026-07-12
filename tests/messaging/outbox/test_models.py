from __future__ import annotations

from uuid import uuid4

from waku.messaging.outbox.models import OutboxMessage


def test_outbox_message_metadata_field_is_metadata() -> None:
    msg = OutboxMessage(
        id=uuid4(),
        idempotency_key=str(uuid4()),
        message_type='test.Event',
        payload={'test': True},
        destination='test://dest',
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        metadata={'k': 'v'},
    )
    assert msg.metadata == {'k': 'v'}
    assert not hasattr(msg, 'metadata_')
