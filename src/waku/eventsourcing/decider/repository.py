from __future__ import annotations

import abc
import logging
import typing
import uuid
from typing import ClassVar, Final, Generic, cast

from waku.eventsourcing._introspection import is_abstract, resolve_generic_args
from waku.eventsourcing._stream_helpers import read_aggregate_stream
from waku.eventsourcing.contracts.aggregate import (  # Dishka needs runtime access
    CommandT,
    EventT,
    IDecider,
    StateT,
)
from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import StreamNotFoundError
from waku.eventsourcing.serialization.interfaces import (
    ISnapshotStateSerializer,  # noqa: TC001  # Dishka needs runtime access
)
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.snapshot.manager import SnapshotManager
from waku.eventsourcing.snapshot.registry import SnapshotConfigRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

__all__ = [
    'DeciderRepository',
    'SnapshotDeciderRepository',
]

logger = logging.getLogger(__name__)

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
        stored_events = await read_aggregate_stream(
            self._event_store,
            stream_id,
            aggregate_name=self.aggregate_name,
            aggregate_id=aggregate_id,
            max_stream_length=self.max_stream_length,
        )
        state = self._decider.initial_state()
        for stored in cast('list[StoredEvent[EventT]]', stored_events):
            state = self._decider.evolve(state, stored.data)
        version = stored_events[-1].position if stored_events else -1
        logger.debug('Loaded %d events for %s/%s', len(stored_events), self.aggregate_name, aggregate_id)
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
        new_version = await self._event_store.append_to_stream(stream_id, envelopes, expected_version=expected)
        logger.debug(
            'Saved %d events to %s/%s, version %d',
            len(events),
            self.aggregate_name,
            aggregate_id,
            new_version,
        )
        return new_version

    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate(self.aggregate_name, aggregate_id)


class SnapshotDeciderRepository(DeciderRepository[StateT, CommandT, EventT], abc.ABC):
    snapshot_state_type: ClassVar[str | None] = None

    def __init__(
        self,
        decider: IDecider[StateT, CommandT, EventT],
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_config_registry: SnapshotConfigRegistry,
        state_serializer: ISnapshotStateSerializer,
    ) -> None:
        super().__init__(decider, event_store)
        self._state_serializer = state_serializer
        self._state_type: type[StateT] = type(self._decider.initial_state())
        config = snapshot_config_registry.get(self.aggregate_name)
        self._snapshot_manager = SnapshotManager(
            store=snapshot_store,
            config=config,
            state_type_name=self.snapshot_state_type or self._state_type.__name__,
        )

    async def load(self, aggregate_id: str) -> tuple[StateT, int]:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_manager.load_snapshot(stream_id, aggregate_id)

        if snapshot is not None:
            logger.debug('Loaded snapshot for %s/%s at version %d', self.aggregate_name, aggregate_id, snapshot.version)
            state = self._state_serializer.deserialize(snapshot.state, self._state_type)
            try:
                stored_events = await self._event_store.read_stream(stream_id, start=snapshot.version + 1)
            except StreamNotFoundError:
                stored_events = []
            for stored in cast('list[StoredEvent[EventT]]', stored_events):
                state = self._decider.evolve(state, stored.data)
            version = stored_events[-1].position if stored_events else snapshot.version
            return state, version

        logger.debug('No snapshot for %s/%s, loading from events', self.aggregate_name, aggregate_id)
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

        if events and self._snapshot_manager.should_save(aggregate_id, new_version):
            if current_state is not None:
                state = current_state
            else:
                state, _ = await self.load(aggregate_id)
            state_data = self._state_serializer.serialize(state)
            stream_id = self._stream_id(aggregate_id)
            await self._snapshot_manager.save_snapshot(stream_id, aggregate_id, state_data, new_version)

        return new_version
