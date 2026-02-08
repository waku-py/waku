from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate


@dataclass(frozen=True)
class TaskCreated(INotification):
    title: str


@dataclass(frozen=True)
class TaskCompleted(INotification):
    pass


class TaskAggregate(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ''
        self.completed: bool = False

    def create(self, title: str) -> None:
        self._raise_event(TaskCreated(title=title))

    def complete(self) -> None:
        if self.completed:
            msg = 'Already completed'
            raise ValueError(msg)
        self._raise_event(TaskCompleted())

    def _apply(self, event: INotification) -> None:
        match event:
            case TaskCreated(title=title):
                self.title = title
            case TaskCompleted():
                self.completed = True


def test_new_aggregate_has_initial_version_and_no_events() -> None:
    aggregate = TaskAggregate()

    assert aggregate.version == -1
    assert aggregate.collect_events() == []


def test_raise_event_applies_event_and_collects() -> None:
    aggregate = TaskAggregate()

    aggregate.create('Write tests')

    assert aggregate.title == 'Write tests'
    assert aggregate.collect_events() == [TaskCreated(title='Write tests')]


def test_multiple_events_accumulate() -> None:
    aggregate = TaskAggregate()

    aggregate.create('Write tests')
    aggregate.complete()

    assert aggregate.collect_events() == [
        TaskCreated(title='Write tests'),
        TaskCompleted(),
    ]


def test_collect_events_is_destructive() -> None:
    aggregate = TaskAggregate()
    aggregate.create('Write tests')
    aggregate.complete()

    first = aggregate.collect_events()
    second = aggregate.collect_events()

    assert len(first) == 2
    assert second == []


def test_load_from_history_reconstructs_state_and_sets_version() -> None:
    aggregate = TaskAggregate()
    history = [TaskCreated(title='From history'), TaskCompleted()]

    aggregate.load_from_history(history, version=5)

    assert aggregate.title == 'From history'
    assert aggregate.completed is True
    assert aggregate.version == 5


def test_load_from_history_does_not_add_to_pending() -> None:
    aggregate = TaskAggregate()
    history = [TaskCreated(title='From history'), TaskCompleted()]

    aggregate.load_from_history(history, version=3)

    assert aggregate.collect_events() == []


def test_create_sets_title_and_complete_sets_completed() -> None:
    aggregate = TaskAggregate()

    aggregate.create('Deploy service')

    assert aggregate.title == 'Deploy service'
    assert aggregate.completed is False

    aggregate.complete()

    assert aggregate.completed is True


def test_completing_already_completed_aggregate_raises_error() -> None:
    aggregate = TaskAggregate()
    aggregate.create('One-shot task')
    aggregate.complete()

    with pytest.raises(ValueError, match='Already completed'):
        aggregate.complete()
