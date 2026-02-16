from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from typing_extensions import override

from waku.eventsourcing.serialization._retort import shared_retort, validate_dataclass_instance
from waku.eventsourcing.serialization.interfaces import IEventSerializer, ISnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.contracts.aggregate import StateT

__all__ = ['JsonEventSerializer', 'JsonSnapshotStateSerializer']


class JsonEventSerializer(IEventSerializer):
    def __init__(self, registry: EventTypeRegistry) -> None:
        self._registry = registry

    @override
    def serialize(self, event: INotification, /) -> dict[str, Any]:
        validate_dataclass_instance(event)
        return cast('dict[str, Any]', shared_retort.dump(event, type(event)))

    @override
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> INotification:
        cls = self._registry.resolve(event_type)
        return shared_retort.load(data, cls)


class JsonSnapshotStateSerializer(ISnapshotStateSerializer):
    @override
    def serialize(self, state: object, /) -> dict[str, Any]:
        validate_dataclass_instance(state)
        return cast('dict[str, Any]', shared_retort.dump(state, type(state)))

    @override
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT:
        return shared_retort.load(data, state_type)
