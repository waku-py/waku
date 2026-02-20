from __future__ import annotations

import abc
import typing
import uuid
from typing import ClassVar, Final, Generic

from waku.eventsourcing._introspection import is_abstract, resolve_generic_args
from waku.eventsourcing.contracts.aggregate import (  # Dishka needs runtime access
    CommandT,
    EventT,
    IDecider,
    StateT,
)
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import (
    AggregateNotFoundError,
    SnapshotTypeMismatchError,
    StreamNotFoundError,
    StreamTooLargeError,
)
from waku.eventsourcing.serialization.interfaces import (
    ISnapshotStateSerializer,  # noqa: TC001  # Dishka needs runtime access
)
from waku.eventsourcing.snapshot.interfaces import (  # Dishka needs runtime access
    ISnapshotStore,
    Snapshot,
)
from waku.eventsourcing.snapshot.migration import migrate_snapshot_or_discard
from waku.eventsourcing.snapshot.registry import SnapshotConfigRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

__all__ = [
    'DeciderRepository',
    'SnapshotDeciderRepository',
]

_STATE_SUFFIX: Final = 'State'


class DeciderRepository(abc.ABC, Generic[StateT, CommandT, EventT]):
    aggregate_name: ClassVar[str]
    max_stream_length: ClassVar[int | None] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if is_abstract(cls):
            return
        if not getattr(cls, 'aggregate_name', None):
            state_cls = cls._resolve_state_type()
            if state_cls is not None:
                name = state_cls.__name__
                if name.endswith(_STATE_SUFFIX) and len(name) > len(_STATE_SUFFIX):
                    name = name.removesuffix(_STATE_SUFFIX)
                cls.aggregate_name = name
            else:
                msg = f'{cls.__name__} must define aggregate_name or parametrize Generic with a concrete state type'
                raise TypeError(msg)

    @classmethod
    def _resolve_state_type(cls) -> type[StateT] | None:
        args = resolve_generic_args(cls, DeciderRepository)
        return args[0] if args else None  # ty: ignore[invalid-return-type]

    def __init__(
        self,
        decider: IDecider[StateT, CommandT, EventT],
        event_store: IEventStore,
    ) -> None:
        self._decider = decider
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> tuple[StateT, int]:
        stream_id = self._stream_id(aggregate_id)
        count = self.max_stream_length + 1 if self.max_stream_length is not None else None
        try:
            stored_events = await self._event_store.read_stream(stream_id, count=count)
        except StreamNotFoundError:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_name,
                aggregate_id=aggregate_id,
            ) from None
        if self.max_stream_length is not None and len(stored_events) > self.max_stream_length:
            raise StreamTooLargeError(stream_id, self.max_stream_length)
        state = self._decider.initial_state()
        for stored in stored_events:
            state = self._decider.evolve(state, stored.data)  # type: ignore[arg-type]
        version = len(stored_events) - 1
        return state, version

    async def save(
        self,
        aggregate_id: str,
        events: typing.Sequence[EventT],
        expected_version: int,
        *,
        current_state: StateT | None = None,  # noqa: ARG002
        idempotency_key: str | None = None,
    ) -> int:
        if not events:
            return expected_version
        stream_id = self._stream_id(aggregate_id)
        envelopes = [
            EventEnvelope(
                domain_event=e,
                idempotency_key=f'{idempotency_key}:{i}' if idempotency_key else str(uuid.uuid4()),
            )
            for i, e in enumerate(events)
        ]
        expected = Exact(version=expected_version) if expected_version >= 0 else NoStream()
        return await self._event_store.append_to_stream(stream_id, envelopes, expected_version=expected)

    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate(self.aggregate_name, aggregate_id)


class SnapshotDeciderRepository(DeciderRepository[StateT, CommandT, EventT], abc.ABC):
    def __init__(
        self,
        decider: IDecider[StateT, CommandT, EventT],
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_config_registry: SnapshotConfigRegistry,
        state_serializer: ISnapshotStateSerializer,
    ) -> None:
        super().__init__(decider, event_store)
        self._snapshot_store = snapshot_store
        self._state_serializer = state_serializer
        self._last_snapshot_versions: dict[str, int] = {}
        self._state_type: type[StateT] = type(self._decider.initial_state())
        snapshot_config = snapshot_config_registry.get(self.aggregate_name)
        self._snapshot_strategy = snapshot_config.strategy
        self._snapshot_schema_version = snapshot_config.schema_version
        self._migration_chain = snapshot_config.migration_chain

    async def load(self, aggregate_id: str) -> tuple[StateT, int]:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_store.load(stream_id)

        if snapshot is not None:
            if snapshot.state_type != self._state_type.__name__:
                raise SnapshotTypeMismatchError(stream_id, self._state_type.__name__, snapshot.state_type)

            if snapshot.schema_version != self._snapshot_schema_version:
                snapshot = migrate_snapshot_or_discard(
                    self._migration_chain,
                    snapshot,
                    self._snapshot_schema_version,
                    stream_id,
                )
                if snapshot is None:
                    self._last_snapshot_versions[aggregate_id] = -1
                    return await super().load(aggregate_id)

            self._last_snapshot_versions[aggregate_id] = snapshot.version
            state = self._state_serializer.deserialize(snapshot.state, self._state_type)
            try:
                stored_events = await self._event_store.read_stream(stream_id, start=snapshot.version + 1)
            except StreamNotFoundError:
                stored_events = []
            for stored in stored_events:
                state = self._decider.evolve(state, stored.data)  # type: ignore[arg-type]
            version = snapshot.version + len(stored_events)
            return state, version

        self._last_snapshot_versions[aggregate_id] = -1
        return await super().load(aggregate_id)

    async def save(
        self,
        aggregate_id: str,
        events: typing.Sequence[EventT],
        expected_version: int,
        *,
        current_state: StateT | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        new_version = await super().save(
            aggregate_id,
            events,
            expected_version,
            current_state=current_state,
            idempotency_key=idempotency_key,
        )

        if events:
            last_snapshot_version = self._last_snapshot_versions.get(aggregate_id, -1)
            events_since_snapshot = new_version - last_snapshot_version
            if self._snapshot_strategy.should_snapshot(new_version, events_since_snapshot):
                if current_state is not None:
                    state = current_state
                else:
                    state, _ = await self.load(aggregate_id)
                state_data = self._state_serializer.serialize(state)
                new_snapshot = Snapshot(
                    stream_id=self._stream_id(aggregate_id),
                    state=state_data,
                    version=new_version,
                    state_type=self._state_type.__name__,
                    schema_version=self._snapshot_schema_version,
                )
                await self._snapshot_store.save(new_snapshot)
                self._last_snapshot_versions[aggregate_id] = new_version

        return new_version
