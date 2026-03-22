from __future__ import annotations

from functools import reduce

import pytest

from tests.eventsourcing.domain import CounterDecider, CounterState, Increment, Incremented


def test_initial_state_returns_default_state() -> None:
    decider = CounterDecider()

    state = decider.initial_state()

    assert state == CounterState(value=0)


def test_decide_produces_events_from_valid_command() -> None:
    decider = CounterDecider()
    state = decider.initial_state()

    events = decider.decide(Increment(amount=5), state)

    assert events == [Incremented(amount=5)]


def test_decide_rejects_invalid_command() -> None:
    decider = CounterDecider()
    state = decider.initial_state()

    with pytest.raises(ValueError, match='Amount must be positive'):
        decider.decide(Increment(amount=0), state)


def test_evolve_applies_event_to_state() -> None:
    decider = CounterDecider()
    state = decider.initial_state()

    new_state = decider.evolve(state, Incremented(amount=3))

    assert new_state == CounterState(value=3)


def test_evolve_folds_multiple_events() -> None:
    decider = CounterDecider()
    events = [Incremented(amount=1), Incremented(amount=2), Incremented(amount=3)]

    final_state = reduce(decider.evolve, events, decider.initial_state())

    assert final_state == CounterState(value=6)
