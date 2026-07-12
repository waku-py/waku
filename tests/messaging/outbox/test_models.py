from __future__ import annotations

from uuid import uuid4

from waku.messaging.outbox.models import OutboxMessage


def _make_message(idempotency_key: str) -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=idempotency_key,
        message_type='test.Event',
        payload={'test': True},
        destination='test://dest',
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        metadata={'k': 'v'},
    )


def test_outbox_message_metadata_field_is_metadata() -> None:
    msg = _make_message(str(uuid4()))
    assert msg.metadata == {'k': 'v'}
    assert not hasattr(msg, 'metadata_')


def test_message_id_parses_valid_idempotency_key() -> None:
    original = uuid4()
    assert _make_message(str(original)).message_id == original


def test_message_id_is_none_for_non_uuid_idempotency_key() -> None:
    assert _make_message('not-a-uuid').message_id is None
