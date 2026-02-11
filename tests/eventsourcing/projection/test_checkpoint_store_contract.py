from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from waku.eventsourcing.projection.checkpoint import Checkpoint

if TYPE_CHECKING:
    from waku.eventsourcing.projection.interfaces import ICheckpointStore


async def test_load_returns_none_when_no_checkpoint(checkpoint_store: ICheckpointStore) -> None:
    result = await checkpoint_store.load('nonexistent')
    assert result is None


async def test_save_and_load_round_trip(checkpoint_store: ICheckpointStore) -> None:
    now = datetime.now(UTC)
    checkpoint = Checkpoint(projection_name='order_summary', position=42, updated_at=now)
    await checkpoint_store.save(checkpoint)

    loaded = await checkpoint_store.load('order_summary')

    assert loaded is not None
    assert loaded.projection_name == 'order_summary'
    assert loaded.position == 42
    assert loaded.updated_at == now


async def test_upsert_replaces_existing(checkpoint_store: ICheckpointStore) -> None:
    t1 = datetime.now(UTC)
    await checkpoint_store.save(Checkpoint(projection_name='order_summary', position=10, updated_at=t1))

    t2 = t1 + timedelta(seconds=30)
    await checkpoint_store.save(Checkpoint(projection_name='order_summary', position=50, updated_at=t2))

    loaded = await checkpoint_store.load('order_summary')

    assert loaded is not None
    assert loaded.position == 50
    assert loaded.updated_at == t2


async def test_separate_projections_have_independent_checkpoints(checkpoint_store: ICheckpointStore) -> None:
    now = datetime.now(UTC)
    await checkpoint_store.save(Checkpoint(projection_name='proj_a', position=10, updated_at=now))
    await checkpoint_store.save(Checkpoint(projection_name='proj_b', position=20, updated_at=now))

    loaded_a = await checkpoint_store.load('proj_a')
    loaded_b = await checkpoint_store.load('proj_b')

    assert loaded_a is not None
    assert loaded_a.position == 10
    assert loaded_b is not None
    assert loaded_b.position == 20
