from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

import anyio
import pytest

from waku.eventsourcing.contracts.aggregate import CommandT, EventSourcedAggregate, EventT, IDecider, StateT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.eventsourcing.projection.interfaces import ICheckpointStore
    from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
    from waku.eventsourcing.store.interfaces import IEventReader
    from waku.messaging.contracts.event import IEvent

__all__ = ['AggregateSpec', 'DeciderSpec', 'wait_for_all_projections', 'wait_for_projection']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class DeciderSpec(Generic[StateT, CommandT, EventT]):
    """Given/When/Then DSL for testing ``IDecider`` implementations.

    Example::

        DeciderSpec.for_(decider).given([event]).when(command).then([expected])
    """

    def __init__(self, decider: IDecider[StateT, CommandT, EventT]) -> None:
        self._decider = decider
        self._events: list[EventT] = []

    @classmethod
    def for_(cls, decider: IDecider[StateT, CommandT, EventT]) -> DeciderSpec[StateT, CommandT, EventT]:
        return cls(decider)

    def given(self, events: Sequence[EventT]) -> DeciderSpec[StateT, CommandT, EventT]:
        self._events = list(events)
        return self

    def when(self, command: CommandT) -> _DeciderWhenResult[StateT, CommandT, EventT]:
        state = self._decider.initial_state()
        for event in self._events:
            state = self._decider.evolve(state, event)
        return _DeciderWhenResult(self._decider, state, command)

    def then_state(self, predicate: Callable[[StateT], bool]) -> None:
        state = self._decider.initial_state()
        for event in self._events:
            state = self._decider.evolve(state, event)
        assert predicate(state), f'State predicate failed for state: {state}'  # noqa: S101


class _DeciderWhenResult(Generic[StateT, CommandT, EventT]):
    def __init__(self, decider: IDecider[StateT, CommandT, EventT], state: StateT, command: CommandT) -> None:
        self._decider = decider
        self._state = state
        self._command = command

    def then(self, expected_events: Sequence[EventT]) -> None:
        actual = self._decider.decide(self._command, self._state)
        assert list(actual) == list(expected_events), f'Expected {expected_events}, got {list(actual)}'  # noqa: S101

    def then_raises(self, exception_type: type[Exception], match: str | None = None) -> None:
        with pytest.raises(exception_type, match=match):
            self._decider.decide(self._command, self._state)

    def then_no_events(self) -> None:
        actual = self._decider.decide(self._command, self._state)
        assert list(actual) == [], f'Expected no events, got {list(actual)}'  # noqa: S101

    @property
    def resulting_state(self) -> StateT:
        events = self._decider.decide(self._command, self._state)
        state = self._state
        for event in events:
            state = self._decider.evolve(state, event)
        return state

    def then_state(self, predicate: Callable[[StateT], Any]) -> None:
        state = self.resulting_state
        assert predicate(state), f'State predicate failed for state: {state}'  # noqa: S101


class AggregateSpec(Generic[AggregateT]):
    """Given/When/Then DSL for testing ``EventSourcedAggregate`` implementations.

    Example::

        AggregateSpec.for_(OrderAggregate).given([OrderCreated(...)]).when(lambda agg: agg.cancel()).then([
            OrderCancelled(...)
        ])
    """

    def __init__(self, aggregate_type: type[AggregateT]) -> None:
        self._aggregate_type = aggregate_type
        self._events: list[IEvent] = []

    @classmethod
    def for_(cls, aggregate_type: type[AggregateT]) -> AggregateSpec[AggregateT]:
        return cls(aggregate_type)

    def given(self, events: Sequence[IEvent]) -> AggregateSpec[AggregateT]:
        self._events = list(events)
        return self

    def when(self, action: Callable[[AggregateT], None]) -> _AggregateWhenResult[AggregateT]:
        aggregate = self._hydrate()
        return _AggregateWhenResult(aggregate, action)

    def then_state(self, predicate: Callable[[AggregateT], Any]) -> None:
        aggregate = self._hydrate()
        assert predicate(aggregate), f'State predicate failed for aggregate: {aggregate}'  # noqa: S101

    def _hydrate(self) -> AggregateT:
        aggregate = self._aggregate_type()
        if self._events:
            aggregate.load_from_history(self._events, version=len(self._events) - 1)
        return aggregate


class _AggregateWhenResult(Generic[AggregateT]):
    def __init__(self, aggregate: AggregateT, action: Callable[[AggregateT], None]) -> None:
        self._aggregate = aggregate
        self._action = action

    def then(self, expected_events: Sequence[IEvent]) -> None:
        self._action(self._aggregate)
        actual = list(self._aggregate.collect_events())
        expected = list(expected_events)
        assert actual == expected, f'Expected {expected}, got {actual}'  # noqa: S101

    def then_raises(self, exception_type: type[Exception], match: str | None = None) -> None:
        with pytest.raises(exception_type, match=match):
            self._action(self._aggregate)

    def then_no_events(self) -> None:
        self._action(self._aggregate)
        actual = list(self._aggregate.collect_events())
        assert actual == [], f'Expected no events, got {actual}'  # noqa: S101

    def then_state(self, predicate: Callable[[AggregateT], Any]) -> None:
        self._action(self._aggregate)
        self._aggregate.collect_events()  # drain pending events
        assert predicate(self._aggregate), f'State predicate failed for aggregate: {self._aggregate}'  # noqa: S101


async def wait_for_projection(
    checkpoint_store: ICheckpointStore,
    event_reader: IEventReader,
    projection_name: str,
    *,
    deadline: float = 5.0,
    poll_interval: float = 0.1,
) -> None:
    """Poll until a projection's checkpoint reaches the global head position.

    Returns immediately when the event store is empty (head position == -1).

    Args:
        checkpoint_store: Store to read projection checkpoints from.
        event_reader: Event reader to determine the global head position.
        projection_name: Name of the projection to wait for.
        deadline: Maximum seconds to wait before raising ``TimeoutError``.
        poll_interval: Seconds between checkpoint polls.

    Raises:
        TimeoutError: If the projection does not catch up within *deadline* seconds.
    """
    head = await event_reader.global_head_position()
    if head == -1:
        return

    try:
        with anyio.fail_after(deadline):
            while True:
                checkpoint = await checkpoint_store.load(projection_name)
                if checkpoint is not None and checkpoint.position >= head:
                    return
                await anyio.sleep(poll_interval)
    except TimeoutError:
        msg = f'Projection {projection_name!r} did not catch up within {deadline}s'
        raise TimeoutError(msg) from None


async def wait_for_all_projections(
    checkpoint_store: ICheckpointStore,
    event_reader: IEventReader,
    projection_registry: CatchUpProjectionRegistry,
    *,
    deadline: float = 10.0,
    poll_interval: float = 0.1,
) -> None:
    """Poll until every registered catch-up projection has caught up.

    Delegates to :func:`wait_for_projection` for each binding in the registry.

    Args:
        checkpoint_store: Store to read projection checkpoints from.
        event_reader: Event reader to determine the global head position.
        projection_registry: Registry of catch-up projection bindings.
        deadline: Maximum seconds to wait *per projection*.
        poll_interval: Seconds between checkpoint polls.

    Raises:
        TimeoutError: If any projection does not catch up within *deadline* seconds.
    """
    for binding in projection_registry:
        await wait_for_projection(
            checkpoint_store=checkpoint_store,
            event_reader=event_reader,
            projection_name=binding.projection.projection_name,
            deadline=deadline,
            poll_interval=poll_interval,
        )
