from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Protocol, TypeVar

from waku.messaging.contracts.event import IEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    'CommandT',
    'EventSourcedAggregate',
    'EventT',
    'IDecider',
    'StateT',
]

StateT = TypeVar('StateT')
CommandT = TypeVar('CommandT', contravariant=True)  # noqa: PLC0105
EventT = TypeVar('EventT', bound=IEvent)


class IDecider(Protocol[StateT, CommandT, EventT]):
    def initial_state(self) -> StateT: ...
    def decide(self, command: CommandT, state: StateT) -> Sequence[EventT]: ...
    def evolve(self, state: StateT, event: EventT) -> StateT: ...


class EventSourcedAggregate(abc.ABC):
    _version: int
    _pending_events: list[IEvent]

    def __init__(self) -> None:
        self._version = -1
        self._pending_events = []

    @property
    def version(self) -> int:
        return self._version

    def collect_events(self) -> list[IEvent]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def mark_persisted(self, version: int) -> None:
        self._version = version

    def _raise_event(self, event: IEvent) -> None:
        self._apply(event)
        self._pending_events.append(event)

    @abc.abstractmethod
    def _apply(self, event: IEvent) -> None: ...

    def load_from_history(self, events: Sequence[IEvent], version: int) -> None:
        for event in events:
            self._apply(event)
        self._version = version
