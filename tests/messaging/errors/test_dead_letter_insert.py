from __future__ import annotations

from uuid import uuid4

from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_insert_values, dead_letter_table
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry, DeadLetterStatus


def test_dead_letter_insert_values_carries_wire_fields() -> None:
    # move_to_dead_letter-persisted rows must stay replayable: the insert carries the full wire
    # field set (message_id, group_id, metadata, destination_kind) alongside the failure columns,
    # plus status/replay_count from the entry itself. A fresh dead letter is PENDING/0.
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type='orders.OrderPlaced',
        payload={'order_id': 'o-1'},
        destination='tests.messaging.OrderHandler',
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id='corr-1',
        causation_id='caus-1',
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
        message_id=uuid4(),
        group_id='partition-7',
        metadata={'trace': 'abc'},
    )

    values = dead_letter_insert_values(entry)

    assert values == {
        'id': entry.id,
        'message_type': 'orders.OrderPlaced',
        'payload': {'order_id': 'o-1'},
        'destination': 'tests.messaging.OrderHandler',
        'destination_kind': DeadLetterDestinationKind.HANDLER,
        'correlation_id': 'corr-1',
        'causation_id': 'caus-1',
        'error_type': 'RuntimeError',
        'error_message': 'boom',
        'retry_count': 3,
        'status': DeadLetterStatus.PENDING,
        'replay_count': 0,
        'message_id': entry.message_id,
        'group_id': 'partition-7',
        'metadata': {'trace': 'abc'},
    }


def test_dead_letter_insert_values_carries_entry_status_and_replay_count() -> None:
    # The single mapping authority must carry the entry's own status/replay_count (not force the
    # column defaults) so a pre-failed entry round-trips its state through move_to_dead_letter.
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type='orders.OrderPlaced',
        payload={'order_id': 'o-1'},
        destination='tests.messaging.OrderHandler',
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id='corr-1',
        causation_id='caus-1',
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
        status=DeadLetterStatus.REPLAY_FAILED,
        replay_count=2,
    )

    values = dead_letter_insert_values(entry)

    assert values['status'] is DeadLetterStatus.REPLAY_FAILED
    assert values['replay_count'] == 2
    assert 'created_at' not in values  # unset created_at keeps the server-side now()


def test_dead_letter_table_has_destination_kind_column() -> None:
    assert 'destination_kind' in dead_letter_table.c
    column = dead_letter_table.c.destination_kind
    assert column.nullable is False
    # No server_default: both write paths (save, dead_letter_insert_values) always supply it.
    assert column.server_default is None
