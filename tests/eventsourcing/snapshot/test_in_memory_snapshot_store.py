from __future__ import annotations

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.snapshot.interfaces import Snapshot


async def test_load_returns_independent_copy() -> None:
    store = InMemorySnapshotStore()
    stream_id = StreamId.for_aggregate('Account', 'acc-1')
    await store.save(Snapshot(stream_id=stream_id, state={'balance': 100}, version=1, state_type='Account'))

    first = await store.load(stream_id)
    assert first is not None
    first.state['balance'] = -1

    second = await store.load(stream_id)
    assert second is not None
    assert second.state == {'balance': 100}


async def test_save_stores_independent_copy() -> None:
    store = InMemorySnapshotStore()
    stream_id = StreamId.for_aggregate('Account', 'acc-1')
    state = {'balance': 100}
    await store.save(Snapshot(stream_id=stream_id, state=state, version=1, state_type='Account'))

    state['balance'] = -1

    loaded = await store.load(stream_id)
    assert loaded is not None
    assert loaded.state == {'balance': 100}
