from __future__ import annotations

from uuid import uuid4

from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_insert_values
from waku.messaging.errors.dead_letter import DeadLetterEntry


def test_dead_letter_insert_values_returns_only_the_nine_persisted_columns() -> None:
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type='orders.OrderPlaced',
        payload={'order_id': 'o-1'},
        destination='local://orders',
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
        'destination': 'local://orders',
        'correlation_id': 'corr-1',
        'causation_id': 'caus-1',
        'error_type': 'RuntimeError',
        'error_message': 'boom',
        'retry_count': 3,
    }
