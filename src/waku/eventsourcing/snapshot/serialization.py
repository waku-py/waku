from __future__ import annotations

import abc
import dataclasses
from typing import Any, TypeVar, cast
from uuid import UUID

from adaptix import Retort, dumper, loader

__all__ = [
    'ISnapshotStateSerializer',
    'JsonSnapshotStateSerializer',
]

StateT = TypeVar('StateT')


class ISnapshotStateSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, state: object, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT: ...


class JsonSnapshotStateSerializer(ISnapshotStateSerializer):
    def __init__(self) -> None:
        self._retort = Retort(
            recipe=[
                loader(UUID, UUID),
                dumper(UUID, str),
            ],
        )

    def serialize(self, state: object, /) -> dict[str, Any]:
        if not dataclasses.is_dataclass(state) or isinstance(state, type):
            msg = f'Expected a dataclass instance, got {type(state).__name__}'
            raise TypeError(msg)
        return cast('dict[str, Any]', self._retort.dump(state, type(state)))

    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT:
        return self._retort.load(data, state_type)
