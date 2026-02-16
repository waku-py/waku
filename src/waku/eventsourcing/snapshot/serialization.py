from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from typing_extensions import override

from waku.eventsourcing.serialization._retort import shared_retort, validate_dataclass_instance
from waku.eventsourcing.snapshot.interfaces import ISnapshotStateSerializer

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.aggregate import StateT

__all__ = [
    'ISnapshotStateSerializer',
    'JsonSnapshotStateSerializer',
]


class JsonSnapshotStateSerializer(ISnapshotStateSerializer):
    @override
    def serialize(self, state: object, /) -> dict[str, Any]:
        validate_dataclass_instance(state)
        return cast('dict[str, Any]', shared_retort.dump(state, type(state)))

    @override
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT:
        return shared_retort.load(data, state_type)
