from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

_TPrimaryKey = TypeVar('_TPrimaryKey')

_sentinel = object()


@dataclass(kw_only=True)
class Entity(Generic[_TPrimaryKey]):  # noqa: PLW1641
    id: _TPrimaryKey = field(default=_sentinel, hash=True)  # type: ignore[assignment]

    @property
    def is_transient(self) -> bool:
        return self.id is _sentinel

    def __eq__(self, other: object) -> bool:
        if self is not other and self.is_transient:
            return False
        return super().__eq__(other)


@dataclass(kw_only=True, frozen=True)
class EventData:
    event_time: datetime


@dataclass(kw_only=True)
class AggregateRoot(Entity[_TPrimaryKey]):
    domain_events: list[EventData] = field(default_factory=list)
