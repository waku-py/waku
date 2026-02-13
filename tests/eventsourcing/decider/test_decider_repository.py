from __future__ import annotations

import pytest

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, ConcurrencyConflictError
from waku.eventsourcing.store.in_memory import InMemoryEventStore  # noqa: TC001  # needed for fixture type

from tests.eventsourcing.decider.conftest import CounterRepository  # noqa: TC002  # needed for fixture type
from tests.eventsourcing.test_decider import CounterState, Incremented


async def test_load_empty_stream_raises_aggregate_not_found_error(
    repository: CounterRepository,
) -> None:
    with pytest.raises(AggregateNotFoundError) as exc_info:
        await repository.load('nonexistent')

    assert exc_info.value.aggregate_id == 'nonexistent'
    assert 'Counter' in exc_info.value.aggregate_type


async def test_save_new_aggregate_persists_events_to_stream(
    repository: CounterRepository,
    event_store: InMemoryEventStore,
) -> None:
    events = [Incremented(amount=5)]

    new_version = await repository.save('c-1', events, expected_version=-1)

    assert new_version == 0
    stream_id = StreamId.for_aggregate('Counter', 'c-1')
    stored = await event_store.read_stream(stream_id)
    assert len(stored) == 1
    assert stored[0].data == Incremented(amount=5)


async def test_save_then_load_roundtrip_folds_state_via_evolve(
    repository: CounterRepository,
) -> None:
    events = [Incremented(amount=3), Incremented(amount=7)]
    await repository.save('c-2', events, expected_version=-1)

    state, version = await repository.load('c-2')

    assert state == CounterState(value=10)
    assert version == 1


async def test_save_with_no_events_returns_expected_version_unchanged(
    repository: CounterRepository,
) -> None:
    await repository.save('c-3', [Incremented(amount=1)], expected_version=-1)

    new_version = await repository.save('c-3', [], expected_version=0)

    assert new_version == 0


async def test_multiple_saves_accumulate_version(
    repository: CounterRepository,
) -> None:
    v1 = await repository.save('c-4', [Incremented(amount=1)], expected_version=-1)
    assert v1 == 0

    v2 = await repository.save('c-4', [Incremented(amount=2), Incremented(amount=3)], expected_version=v1)
    assert v2 == 2

    state, version = await repository.load('c-4')
    assert state == CounterState(value=6)
    assert version == 2


async def test_concurrent_save_raises_concurrency_conflict_error(
    repository: CounterRepository,
) -> None:
    await repository.save('c-5', [Incremented(amount=1)], expected_version=-1)

    _, version_a = await repository.load('c-5')
    _, version_b = await repository.load('c-5')

    await repository.save('c-5', [Incremented(amount=10)], expected_version=version_a)

    with pytest.raises(ConcurrencyConflictError):
        await repository.save('c-5', [Incremented(amount=20)], expected_version=version_b)


async def test_stream_id_uses_aggregate_name(
    repository: CounterRepository,
    event_store: InMemoryEventStore,
) -> None:
    await repository.save('abc-123', [Incremented(amount=1)], expected_version=-1)

    stream_id = StreamId.for_aggregate('Counter', 'abc-123')
    assert await event_store.stream_exists(stream_id)
