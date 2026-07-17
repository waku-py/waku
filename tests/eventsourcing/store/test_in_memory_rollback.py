from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.backends.testing import ItemAdded, OrderCreated
from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamId
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from typing import Any

    from waku.messages import IEvent


def make_envelope(event: IEvent) -> EventEnvelope:
    return EventEnvelope(domain_event=event, idempotency_key=str(uuid.uuid4()))


def _make_store(
    registry: EventTypeRegistry,
    projections: Sequence[IProjection] = (),
) -> InMemoryEventStore:
    return InMemoryEventStore(registry=registry, projections=projections)


@pytest.fixture
def registry() -> EventTypeRegistry:
    r = EventTypeRegistry()
    r.register(OrderCreated)
    r.register(ItemAdded)
    r.freeze()
    return r


class _FailingProjection(IProjection):
    projection_name = 'failing'

    @override
    async def project(self, _events: Sequence[StoredEvent], /) -> None:
        raise RuntimeError


async def test_rollback_new_stream(registry: EventTypeRegistry) -> None:
    store = _make_store(registry, projections=[_FailingProjection()])
    stream_id = StreamId.for_aggregate('Order', 'r-1')

    with pytest.raises(RuntimeError):
        await store.append_to_stream(
            stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream()
        )

    assert not await store.stream_exists(stream_id)
    assert await store.read_all() == []


async def test_rollback_existing_stream(registry: EventTypeRegistry) -> None:
    call_count = 0

    class _FailOnSecondCall(IProjection):
        projection_name = 'fail_second'

        @override
        async def project(self, _events: Sequence[StoredEvent], /) -> None:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError

    store = _make_store(registry, projections=[_FailOnSecondCall()])
    stream_id = StreamId.for_aggregate('Order', 'r-2')

    version = await store.append_to_stream(
        stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream()
    )
    assert version == 0

    with pytest.raises(RuntimeError):
        await store.append_to_stream(
            stream_id, [make_envelope(ItemAdded(item_name='Widget'))], expected_version=Exact(version=0)
        )

    events = await store.read_stream(stream_id)
    assert len(events) == 1
    assert events[0].event_type == 'OrderCreated'


async def test_inline_projection_may_read_back_from_store(registry: EventTypeRegistry) -> None:
    seen: list[StoredEvent] = []

    class _ReadBackProjection(IProjection):
        projection_name = 'read_back'

        @override
        async def project(self, _events: Sequence[StoredEvent], /) -> None:
            seen.extend(await store.read_all())

    store = _make_store(registry, projections=[_ReadBackProjection()])
    stream_id = StreamId.for_aggregate('Order', 'rb-1')

    with anyio.fail_after(1):
        version = await store.append_to_stream(
            stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream()
        )

    assert version == 0
    assert [e.event_type for e in seen] == ['OrderCreated']


class _ParkThenFailOnOrder(IProjection):
    projection_name = 'park_then_fail'

    def __init__(self, order_id: str) -> None:
        self._order_id = order_id
        self.entered = anyio.Event()
        self.release = anyio.Event()

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        if not any(isinstance(e.data, OrderCreated) and e.data.order_id == self._order_id for e in events):
            return
        self.entered.set()
        await self.release.wait()
        raise RuntimeError


async def _park_first_then_race(
    projection: _ParkThenFailOnOrder,
    append_first: Callable[[], Coroutine[Any, Any, None]],
    append_second: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    with anyio.fail_after(2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(append_first)
            await projection.entered.wait()
            tg.start_soon(append_second)
            await anyio.wait_all_tasks_blocked()
            projection.release.set()


async def test_concurrent_same_stream_append_never_observes_rolled_back_events(
    registry: EventTypeRegistry,
) -> None:
    projection = _ParkThenFailOnOrder('A')
    store = _make_store(registry, projections=[projection])
    stream_id = StreamId.for_aggregate('Order', 'il-1')
    a_errors: list[Exception] = []
    b_versions: list[int] = []

    async def append_a() -> None:
        try:
            await store.append_to_stream(
                stream_id, [make_envelope(OrderCreated(order_id='A'))], expected_version=NoStream()
            )
        except RuntimeError as exc:
            a_errors.append(exc)

    async def append_b() -> None:
        b_versions.append(
            await store.append_to_stream(
                stream_id, [make_envelope(OrderCreated(order_id='B'))], expected_version=AnyVersion()
            )
        )

    await _park_first_then_race(projection, append_a, append_b)

    assert len(a_errors) == 1

    events = await store.read_stream(stream_id)
    assert [e.data.order_id for e in events if isinstance(e.data, OrderCreated)] == ['B']
    assert [e.position for e in events] == [0]
    assert b_versions == [0]

    follow_up_version = await store.append_to_stream(
        stream_id, [make_envelope(ItemAdded(item_name='Widget'))], expected_version=Exact(version=b_versions[0])
    )
    assert follow_up_version == 1

    events = await store.read_stream(stream_id)
    assert [e.position for e in events] == [0, 1]
    global_positions = [e.global_position for e in events]
    assert global_positions == sorted(set(global_positions))


async def test_concurrent_other_stream_append_survives_rollback_with_burned_position(
    registry: EventTypeRegistry,
) -> None:
    projection = _ParkThenFailOnOrder('A')
    store = _make_store(registry, projections=[projection])
    failing_stream = StreamId.for_aggregate('Order', 'il-2a')
    surviving_stream = StreamId.for_aggregate('Order', 'il-2b')
    a_errors: list[Exception] = []

    async def append_a() -> None:
        try:
            await store.append_to_stream(
                failing_stream, [make_envelope(OrderCreated(order_id='A'))], expected_version=NoStream()
            )
        except RuntimeError as exc:
            a_errors.append(exc)

    async def append_b() -> None:
        await store.append_to_stream(
            surviving_stream, [make_envelope(OrderCreated(order_id='B'))], expected_version=NoStream()
        )

    await _park_first_then_race(projection, append_a, append_b)

    assert len(a_errors) == 1
    assert not await store.stream_exists(failing_stream)

    survivors = await store.read_all()
    assert [e.data.order_id for e in survivors if isinstance(e.data, OrderCreated)] == ['B']
    assert [e.position for e in survivors] == [0]
    assert survivors[0].global_position == 1


class _FailOnceProjection(IProjection):
    projection_name = 'fail_once'

    def __init__(self) -> None:
        self.burned: list[int] = []
        self._failed = False

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        if not self._failed:
            self._failed = True
            self.burned.extend(e.global_position for e in events)
            raise RuntimeError


async def test_rolled_back_positions_are_not_reused(registry: EventTypeRegistry) -> None:
    projection = _FailOnceProjection()
    store = _make_store(registry, projections=[projection])
    stream_id = StreamId.for_aggregate('Order', 'burn-1')

    with pytest.raises(RuntimeError):
        await store.append_to_stream(
            stream_id, [make_envelope(OrderCreated(order_id='1'))], expected_version=NoStream()
        )

    await store.append_to_stream(stream_id, [make_envelope(OrderCreated(order_id='2'))], expected_version=NoStream())

    events = await store.read_all()
    assert len(events) == 1
    assert events[0].global_position > max(projection.burned)
