from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.eventsourcing.serialization._retort import es_default_retort, validate_dataclass_instance
from waku.eventsourcing.serialization.interfaces import IEventSerializer, ISnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.serialization.codec import PayloadCodec
from waku.serialization.upcasting import UpcasterChain

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.aggregate import StateT
    from waku.messages import IEvent

__all__ = ['JsonEventSerializer', 'JsonSnapshotStateSerializer']

_ES_CODEC = PayloadCodec(es_default_retort, UpcasterChain({}))


class JsonEventSerializer(IEventSerializer):
    def __init__(self, registry: EventTypeRegistry) -> None:
        self._registry = registry

    @override
    def serialize(self, event: IEvent, /) -> dict[str, Any]:
        validate_dataclass_instance(event)
        return _ES_CODEC.encode(event, type(event))

    @override
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> IEvent:
        cls = self._registry.resolve(event_type)
        return _ES_CODEC.load(data, cls)


class JsonSnapshotStateSerializer(ISnapshotStateSerializer):
    @override
    def serialize(self, state: object, /) -> dict[str, Any]:
        validate_dataclass_instance(state)
        return _ES_CODEC.encode(state, type(state))

    @override
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT:
        return _ES_CODEC.load(data, state_type)
