from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent

__all__ = ['IProjection']


class IProjection(abc.ABC):
    @abc.abstractmethod
    async def project(self, events: Sequence[StoredEvent], /) -> None: ...
