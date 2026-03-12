from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.aggregate import StateT
    from waku.messaging.contracts.event import IEvent

__all__ = ['IEventSerializer', 'ISnapshotStateSerializer']


class IEventSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, event: IEvent, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> IEvent: ...


class ISnapshotStateSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, state: object, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT: ...
