from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.aggregate import IDecider


@dataclass(frozen=True)
class CounterState:
    value: int = 0


@dataclass(frozen=True)
class Increment:
    amount: int = 1


@dataclass(frozen=True)
class Incremented:
    amount: int


class CounterDecider:
    def initial_state(self) -> CounterState:  # noqa: PLR6301
        return CounterState()

    def decide(self, command: Increment, state: CounterState) -> list[Incremented]:  # noqa: ARG002, PLR6301
        if command.amount <= 0:
            msg = 'Amount must be positive'
            raise ValueError(msg)
        return [Incremented(amount=command.amount)]

    def evolve(self, state: CounterState, event: Incremented) -> CounterState:  # noqa: PLR6301
        return CounterState(value=state.value + event.amount)


if TYPE_CHECKING:

    def _assert_protocol_conformance(decider: CounterDecider) -> IDecider[CounterState, Increment, Incremented]:
        return decider


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
