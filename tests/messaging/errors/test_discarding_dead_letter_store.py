from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from waku.messaging.errors._internal.discarding_store import DiscardingDeadLetterStore  # noqa: PLC2701
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery

_DISCARDING_LOGGER = 'waku.messaging.errors._internal.discarding_store'


def _entry() -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='tests.OrderPlaced',
        payload={'order_id': 'o-1'},
        destination='local://orders',
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        exc=RuntimeError('boom'),
        attempt=3,
    )


async def test_save_warns_naming_the_message_and_persists_nothing(caplog: pytest.LogCaptureFixture) -> None:
    store = DiscardingDeadLetterStore()
    with caplog.at_level(logging.WARNING, logger=_DISCARDING_LOGGER):
        await store.save(_entry())
    assert 'not persisted' in caplog.text.lower()
    assert 'tests.OrderPlaced' in caplog.text
    assert await store.fetch() == ()
    assert await store.query(DeadLetterQuery()) == ()
    assert await store.claim_replayable(batch_size=10, max_replay_count=3) == ()


async def test_purge_returns_zero() -> None:
    assert await DiscardingDeadLetterStore().purge(datetime.now(tz=UTC)) == 0


async def test_mutations_leave_the_store_empty() -> None:
    store = DiscardingDeadLetterStore()
    entry_id = uuid4()
    await store.mark_replayed(entry_id)
    await store.mark_replay_failed(entry_id, 'err')
    await store.delete(entry_id)
    assert await store.fetch() == ()


async def test_fetch_one_raises_key_error() -> None:
    with pytest.raises(KeyError):
        await DiscardingDeadLetterStore().fetch_one(uuid4())
