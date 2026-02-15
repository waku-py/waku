from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification

__all__ = ['IEventSerializer']


class IEventSerializer(abc.ABC):
    @abc.abstractmethod
    def serialize(self, event: INotification, /) -> dict[str, Any]: ...

    @abc.abstractmethod
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> INotification: ...
