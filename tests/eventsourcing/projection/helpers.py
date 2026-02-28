from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.store.in_memory import InMemoryEventStore


@dataclass(frozen=True)
class SampleEvent(INotification):
    value: int


@dataclass(frozen=True)
class OtherEvent(INotification):
    label: str


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

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        msg = 'projection error'
        raise RuntimeError(msg)


async def seed_events(store: InMemoryEventStore, count: int = 5) -> None:
    stream_id = StreamId(stream_type='test', stream_key='1')
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=SampleEvent(value=i), idempotency_key=f'seed-{i}') for i in range(count)],
        expected_version=NoStream(),
    )


def make_binding(
    projection: type[ICatchUpProjection],
    *,
    error_policy: ErrorPolicy = ErrorPolicy.STOP,
    max_retry_attempts: int = 0,
    base_retry_delay_seconds: float = 10.0,
    max_retry_delay_seconds: float = 300.0,
    batch_size: int = 100,
    event_type_names: tuple[str, ...] | None = None,
) -> CatchUpProjectionBinding:
    return CatchUpProjectionBinding(
        projection=projection,
        error_policy=error_policy,
        max_retry_attempts=max_retry_attempts,
        base_retry_delay_seconds=base_retry_delay_seconds,
        max_retry_delay_seconds=max_retry_delay_seconds,
        batch_size=batch_size,
        event_type_names=event_type_names,
    )


async def seed_mixed_events(store: InMemoryEventStore) -> None:
    stream_id = StreamId(stream_type='test', stream_key='mixed')
    await store.append_to_stream(
        stream_id,
        [
            EventEnvelope(domain_event=SampleEvent(value=0), idempotency_key='mix-0'),
            EventEnvelope(domain_event=OtherEvent(label='a'), idempotency_key='mix-1'),
            EventEnvelope(domain_event=SampleEvent(value=1), idempotency_key='mix-2'),
            EventEnvelope(domain_event=OtherEvent(label='b'), idempotency_key='mix-3'),
        ],
        expected_version=NoStream(),
    )
