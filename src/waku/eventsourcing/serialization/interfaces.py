from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.contracts.aggregate import StateT

__all__ = ['IEventSerializer', 'ISnapshotStateSerializer']


class IEventSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, event: INotification, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> INotification: ...


class ISnapshotStateSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, state: object, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], state_type: type[StateT], /) -> StateT: ...
