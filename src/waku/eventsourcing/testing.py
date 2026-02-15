from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic

import pytest

from waku.eventsourcing.contracts.aggregate import CommandT, EventT, IDecider, StateT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ['DeciderSpec']


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
