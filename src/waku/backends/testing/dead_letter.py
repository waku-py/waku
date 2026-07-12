from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku.messaging.errors.dead_letter import DeadLetterEntry

if TYPE_CHECKING:
    from waku.messaging.durability import IDeadLetterStore

__all__ = ['DeadLetterStoreContract']


def _make_entry(**overrides: object) -> DeadLetterEntry:
    defaults: dict[str, object] = {
        'id': uuid4(),
        'message_type': 'test.FailedEvent',
        'payload': {'key': 'value'},
        'destination': 'test://dead',
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
        'error_type': 'RuntimeError',
        'error_message': 'boom',
        'retry_count': 1,
    }
    return DeadLetterEntry(**(defaults | overrides))  # type: ignore[arg-type]


class DeadLetterStoreContract:
    """Behavioral contract every ``IDeadLetterStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``dlq_store`` fixture with your
    adapter over a fresh resource per test.
    """

    @pytest.fixture
    def dlq_store(self) -> IDeadLetterStore:
        msg = 'override the dlq_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_non_uuid_correlation_causation_round_trip(self, dlq_store: IDeadLetterStore) -> None:
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        entry = _make_entry(correlation_id='trace-abc-123', causation_id='req-xyz-789')
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].correlation_id == 'trace-abc-123'
        assert fetched[0].causation_id == 'req-xyz-789'

    async def test_save_and_fetch_returns_stored_entry(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].id == entry.id

    async def test_p2_columns_metadata_group_id_message_id_round_trip(self, dlq_store: IDeadLetterStore) -> None:
        # Contract: P2 decomposition columns survive the save→fetch cycle for both fake and SQLAlchemy stores.
        original_message_id = uuid4()
        meta = {'message_version': 2, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-tenant': 'acme'}}
        entry = _make_entry(
            group_id='partition-42',
            metadata_=meta,
            message_id=original_message_id,
        )

        await dlq_store.save(entry)
        fetched = await dlq_store.fetch(batch_size=10)

        assert fetched[0].group_id == 'partition-42'
        assert fetched[0].metadata_ == meta
        assert fetched[0].message_id == original_message_id

    async def test_message_id_none_when_not_provided(self, dlq_store: IDeadLetterStore) -> None:
        # Legacy rows (before message_id column) have message_id=None.
        entry = _make_entry()
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert fetched[0].message_id is None
