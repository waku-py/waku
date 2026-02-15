from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.store.in_memory import InMemoryEventStore


@dataclass(frozen=True)
class SampleEvent(INotification):
    value: int


class RecordingProjection(ICatchUpProjection):
    projection_name = 'recording'

    def __init__(self) -> None:
        self.received: list[StoredEvent] = []
        self.teardown_called = False

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        self.received.extend(events)

    @override
    async def teardown(self) -> None:
        self.teardown_called = True
        self.received.clear()


class StopProjection(ICatchUpProjection):
    projection_name = 'stop_proj'
    error_policy: ClassVar[ErrorPolicy] = ErrorPolicy.STOP

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        msg = 'projection error'
        raise RuntimeError(msg)


def make_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(SampleEvent)
    registry.freeze()
    return registry


async def seed_events(store: InMemoryEventStore, count: int = 5) -> None:
    stream_id = StreamId(stream_type='test', stream_key='1')
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=SampleEvent(value=i)) for i in range(count)],
        expected_version=NoStream(),
    )
