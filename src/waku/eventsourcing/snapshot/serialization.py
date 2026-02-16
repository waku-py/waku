from __future__ import annotations

from typing import Any, TypeVar, cast

from typing_extensions import override

from waku.eventsourcing.serialization._retort import shared_retort, validate_dataclass_instance
from waku.eventsourcing.snapshot.interfaces import ISnapshotStateSerializer

__all__ = [
    'ISnapshotStateSerializer',
    'JsonSnapshotStateSerializer',
]

StateT = TypeVar('StateT')


class JsonSnapshotStateSerializer(ISnapshotStateSerializer):
    @override
    def serialize(self, state: object, /) -> dict[str, Any]:
        validate_dataclass_instance(state)
        return cast('dict[str, Any]', shared_retort.dump(state, type(state)))

    @override
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT:
        return shared_retort.load(data, state_type)
