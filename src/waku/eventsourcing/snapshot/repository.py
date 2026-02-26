from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.exceptions import StreamNotFoundError
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.interfaces import (
    ISnapshotStateSerializer,  # noqa: TC001  # Dishka needs runtime access
)
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.snapshot.manager import SnapshotManager
from waku.eventsourcing.snapshot.registry import SnapshotConfigRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.snapshot.interfaces import Snapshot

__all__ = ['SnapshotEventSourcedRepository']

logger = logging.getLogger(__name__)

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class SnapshotEventSourcedRepository(EventSourcedRepository[AggregateT], abc.ABC, Generic[AggregateT]):
    snapshot_state_type: ClassVar[str | None] = None

    def __init__(
        self,
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_config_registry: SnapshotConfigRegistry,
        state_serializer: ISnapshotStateSerializer,
    ) -> None:
        super().__init__(event_store)
        self._state_serializer = state_serializer
        config = snapshot_config_registry.get(self.aggregate_name)
        self._snapshot_manager = SnapshotManager(
            store=snapshot_store,
            config=config,
            state_type_name=self.snapshot_state_type or self.aggregate_name,
        )

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_manager.load_snapshot(stream_id, aggregate_id)

        if snapshot is not None:
            logger.debug('Loaded snapshot for %s/%s at version %d', self.aggregate_name, aggregate_id, snapshot.version)
            aggregate = self._restore_from_snapshot(snapshot)
            start = snapshot.version + 1
            try:
                stored_events = await self._event_store.read_stream(stream_id, start=start)
            except StreamNotFoundError:
                stored_events = []
            domain_events = [e.data for e in stored_events]
            version = stored_events[-1].position if stored_events else snapshot.version
            if domain_events:
                aggregate.load_from_history(domain_events, version)
            else:
                aggregate.mark_persisted(version)
            return aggregate

        logger.debug('No snapshot for %s/%s, loading from events', self.aggregate_name, aggregate_id)
        return await super().load(aggregate_id)

    async def save(
        self,
        aggregate_id: str,
        aggregate: AggregateT,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, list[INotification]]:
        new_version, events = await super().save(aggregate_id, aggregate, idempotency_key=idempotency_key)

        if events and self._snapshot_manager.should_save(aggregate_id, new_version):
            stream_id = self._stream_id(aggregate_id)
            state_obj = self._snapshot_state(aggregate)
            state_data = self._state_serializer.serialize(state_obj)
            await self._snapshot_manager.save_snapshot(stream_id, aggregate_id, state_data, new_version)

        return new_version, events

    @abc.abstractmethod
    def _snapshot_state(self, aggregate: AggregateT) -> object: ...

    @abc.abstractmethod
    def _restore_from_snapshot(self, snapshot: Snapshot) -> AggregateT: ...
