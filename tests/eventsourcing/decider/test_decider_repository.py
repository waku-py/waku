from __future__ import annotations

import pytest

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.decider.repository import DeciderRepository
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamTooLargeError
from waku.eventsourcing.store.in_memory import InMemoryEventStore  # noqa: TC001  # needed for fixture type

from tests.eventsourcing.decider.conftest import (  # noqa: TC002  # needed for fixture type
    CounterRepository,
    LimitedCounterRepository,
)
from tests.eventsourcing.test_decider import CounterState, Increment, Incremented


async def test_load_empty_stream_returns_initial_state_with_version_minus_one(
    repository: CounterRepository,
) -> None:
    state, version = await repository.load('nonexistent')

    assert state == CounterState()
    assert version == -1


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


def test_auto_resolves_aggregate_name_from_state_type() -> None:
    class AutoRepo(DeciderRepository[CounterState, Increment, Incremented]):
        pass

    assert AutoRepo.aggregate_name == 'Counter'


def test_explicit_aggregate_name_takes_precedence() -> None:
    class ExplicitRepo(DeciderRepository[CounterState, Increment, Incremented]):
        aggregate_name = 'MyCounter'

    assert ExplicitRepo.aggregate_name == 'MyCounter'


async def test_load_raises_stream_too_large_error_when_stream_exceeds_limit(
    limited_repository: LimitedCounterRepository,
) -> None:
    events = [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3), Incremented(amount=4)]
    await limited_repository.save('c-big', events, expected_version=-1)

    with pytest.raises(StreamTooLargeError) as exc_info:
        await limited_repository.load('c-big')

    assert exc_info.value.stream_id == StreamId.for_aggregate('Counter', 'c-big')
    assert exc_info.value.max_length == 3


async def test_load_succeeds_when_stream_within_limit(
    limited_repository: LimitedCounterRepository,
) -> None:
    events = [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)]
    await limited_repository.save('c-ok', events, expected_version=-1)

    state, version = await limited_repository.load('c-ok')

    assert state == CounterState(value=6)
    assert version == 2


async def test_save_with_idempotency_key_generates_indexed_keys(
    repository: CounterRepository,
    event_store: InMemoryEventStore,
) -> None:
    events = [Incremented(amount=3), Incremented(amount=7)]

    await repository.save('c-idem-1', events, expected_version=-1, idempotency_key='cmd-456')

    stream_id = StreamId.for_aggregate('Counter', 'c-idem-1')
    stored = await event_store.read_stream(stream_id)
    assert [e.idempotency_key for e in stored] == ['cmd-456:0', 'cmd-456:1']


async def test_save_with_same_idempotency_key_is_idempotent(
    repository: CounterRepository,
    event_store: InMemoryEventStore,
) -> None:
    events = [Incremented(amount=5), Incremented(amount=10)]
    first_version = await repository.save('c-idem-2', events, expected_version=-1, idempotency_key='cmd-789')

    retry_version = await repository.save('c-idem-2', events, expected_version=-1, idempotency_key='cmd-789')

    assert retry_version == first_version
    stream_id = StreamId.for_aggregate('Counter', 'c-idem-2')
    stored = await event_store.read_stream(stream_id)
    assert len(stored) == 2
    state, _ = await repository.load('c-idem-2')
    assert state == CounterState(value=15)


async def test_save_without_idempotency_key_generates_unique_uuid_keys(
    repository: CounterRepository,
    event_store: InMemoryEventStore,
) -> None:
    events = [Incremented(amount=1), Incremented(amount=2)]

    await repository.save('c-idem-3', events, expected_version=-1)

    stream_id = StreamId.for_aggregate('Counter', 'c-idem-3')
    stored = await event_store.read_stream(stream_id)
    keys = [e.idempotency_key for e in stored]
    assert keys[0] != keys[1]
    for key in keys:
        assert len(key) == 36
        assert key.count('-') == 4
