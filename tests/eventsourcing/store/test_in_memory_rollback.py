from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.store.domain import ItemAdded, OrderCreated

if TYPE_CHECKING:
    from collections.abc import Sequence


def make_envelope(event: object) -> EventEnvelope:
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
