from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
import anyio.lowlevel
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ProjectionErrorPolicy
from waku.eventsourcing.store.interfaces import ICheckpointStore
from waku.messages import IEvent
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.checkpoint import Checkpoint
    from waku.eventsourcing.store.in_memory import InMemoryEventStore


@dataclass(frozen=True)
class SampleEvent(IEvent):
    value: int


@dataclass(frozen=True)
class OtherEvent(IEvent):
    label: str


def sample_event_values(events: Sequence[StoredEvent]) -> list[int]:
    values: list[int] = []
    for event in events:
        assert isinstance(event.data, SampleEvent)
        values.append(event.data.value)
    return values


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


def make_binding(  # noqa: PLR0913
    projection: type[ICatchUpProjection],
    *,
    error_policy: ProjectionErrorPolicy = ProjectionErrorPolicy.STOP,
    max_retry_attempts: int = 0,
    base_retry_delay_seconds: float = 10.0,
    max_retry_delay_seconds: float = 300.0,
    batch_size: int = 100,
    event_type_names: tuple[str, ...] | None = None,
    gap_detection_enabled: bool = True,
    gap_timeout_seconds: float = 10.0,
) -> CatchUpProjectionBinding:
    return CatchUpProjectionBinding(
        projection=projection,
        error_policy=error_policy,
        max_retry_attempts=max_retry_attempts,
        base_retry_delay_seconds=base_retry_delay_seconds,
        max_retry_delay_seconds=max_retry_delay_seconds,
        batch_size=batch_size,
        event_type_names=event_type_names,
        gap_detection_enabled=gap_detection_enabled,
        gap_timeout_seconds=gap_timeout_seconds,
    )


class _SessionAbortedError(Exception):
    pass


# Models the scoped AsyncSession shared by checkpoint store, UoW, and (optionally) a projection.
# Writes buffer as pending until commit() promotes them to durable state; abort() puts the session
# into a pending-rollback state (any subsequent write or commit raises, as SQLAlchemy does) until
# rollback() resets it. Loading a checkpoint does not clean pending state: transaction ownership tests
# must prove an explicit rollback rather than receiving cleanup as a side effect of the next read.
class FakeSession:
    def __init__(self) -> None:
        self.aborted = False
        self._pending_checkpoints: dict[str, Checkpoint] = {}
        self._durable_checkpoints: dict[str, Checkpoint] = {}
        self._pending_writes: list[list[int]] = []
        self._durable_writes: list[list[int]] = []

    def abort(self) -> None:
        self.aborted = True

    def write(self, row: list[int]) -> None:
        self._ensure_not_aborted()
        self._pending_writes.append(row)

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._ensure_not_aborted()
        self._pending_checkpoints[checkpoint.projection_name] = checkpoint

    def load_checkpoint(self, projection_name: str) -> Checkpoint | None:
        return self._durable_checkpoints.get(projection_name)

    def commit(self) -> None:
        self._ensure_not_aborted()
        self._durable_checkpoints.update(self._pending_checkpoints)
        self._durable_writes.extend(self._pending_writes)
        self._pending_checkpoints.clear()
        self._pending_writes.clear()

    def rollback(self) -> None:
        self.aborted = False
        self._pending_checkpoints.clear()
        self._pending_writes.clear()

    def durable_checkpoint(self, projection_name: str) -> Checkpoint | None:
        return self._durable_checkpoints.get(projection_name)

    def durable_writes(self) -> list[list[int]]:
        return list(self._durable_writes)

    def _ensure_not_aborted(self) -> None:
        if self.aborted:  # pragma: no cover - tripwire: the runner rolls back before any op on an aborted session
            msg = 'session is in pending-rollback state'
            raise _SessionAbortedError(msg)


class CommitGatedCheckpointStore(ICheckpointStore):
    def __init__(
        self,
        session: FakeSession,
        *,
        save_failures: dict[int, BaseException] | None = None,
    ) -> None:
        self._session = session
        self._save_failures = save_failures or {}
        self.save_count = 0

    @override
    async def load(self, projection_name: str, /) -> Checkpoint | None:
        return self._session.load_checkpoint(projection_name)

    @override
    async def save(self, checkpoint: Checkpoint, /) -> None:
        self.save_count += 1
        if error := self._save_failures.get(self.save_count):
            raise error
        self._session.save_checkpoint(checkpoint)


class CommitGatedUnitOfWork(IUnitOfWork):
    def __init__(
        self,
        session: FakeSession,
        *,
        trace: list[str] | None = None,
        commit_failures: dict[int, BaseException] | None = None,
        rollback_failures: dict[int, BaseException] | None = None,
        cancel_commit_at: int | None = None,
        cancel_scope: anyio.CancelScope | None = None,
    ) -> None:
        self._session = session
        self._trace = trace
        self._commit_failures = commit_failures or {}
        self._rollback_failures = rollback_failures or {}
        self._cancel_commit_at = cancel_commit_at
        self._cancel_scope = cancel_scope
        self.commit_count = 0
        self.rollback_count = 0

    @override
    async def commit(self) -> None:
        self.commit_count += 1
        if self._trace is not None:
            self._trace.append(f'commit-{self.commit_count}')
        if self.commit_count == self._cancel_commit_at:
            if self._cancel_scope is None:  # pragma: no cover - invalid test-double setup
                msg = 'cancel_scope is required when cancel_commit_at is set'
                raise RuntimeError(msg)
            self._cancel_scope.cancel()
            await anyio.lowlevel.checkpoint()
        if error := self._commit_failures.get(self.commit_count):
            raise error
        self._session.commit()

    @override
    async def rollback(self) -> None:
        self.rollback_count += 1
        await anyio.lowlevel.checkpoint()
        if self._trace is not None:
            self._trace.append('rollback')
        if error := self._rollback_failures.get(self.rollback_count):
            raise error
        self._session.rollback()


class PoisonProjection(ICatchUpProjection):
    projection_name = 'poison'

    def __init__(
        self,
        poison_value: int,
        *,
        session: FakeSession | None = None,
        on_skip_fails: bool = False,
    ) -> None:
        self._poison_value = poison_value
        self._session = session
        self._on_skip_fails = on_skip_fails
        self.batches: list[list[StoredEvent]] = []
        self.skipped: list[list[StoredEvent]] = []

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        self.batches.append(list(events))
        values = sample_event_values(events)
        if self._session is not None:
            self._session.write(values)
        if self._poison_value in values:
            if self._session is not None:
                self._session.abort()
            msg = f'poison value {self._poison_value} in batch'
            raise RuntimeError(msg)

    @override
    async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
        self.skipped.append(list(events))
        if self._on_skip_fails:
            if self._session is not None:
                # Models an on_skip that writes a skip-audit row then hits an IntegrityError, aborting
                # the shared session: the runner must roll back again before saving the checkpoint.
                self._session.write([-1])
                self._session.abort()
            msg = 'on_skip also fails'
            raise RuntimeError(msg)


class FlakyProjection(ICatchUpProjection):
    projection_name = 'flaky'

    def __init__(self, failures: int) -> None:
        self._remaining_failures = failures
        self.received: list[StoredEvent] = []

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        # Raise BEFORE recording, so a failed attempt contributes nothing to `received`.
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            msg = 'transient failure'
            raise RuntimeError(msg)
        self.received.extend(events)


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
