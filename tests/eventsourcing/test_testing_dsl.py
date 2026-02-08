from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

import pytest

from waku.eventsourcing.testing import DeciderSpec


@dataclass(frozen=True)
class CounterState:
    value: int = 0


@dataclass(frozen=True)
class Increment:
    amount: int = 1


@dataclass(frozen=True)
class Decrement:
    amount: int = 1


@dataclass(frozen=True)
class Noop:
    pass


@dataclass(frozen=True)
class Incremented:
    amount: int


@dataclass(frozen=True)
class Decremented:
    amount: int


class CounterDecider:
    def initial_state(self) -> CounterState:  # noqa: PLR6301
        return CounterState()

    def decide(  # noqa: PLR6301
        self,
        command: Increment | Decrement | Noop,
        state: CounterState,
    ) -> list[Incremented | Decremented]:
        match command:
            case Increment(amount=a):
                if a <= 0:
                    msg = 'Amount must be positive'
                    raise ValueError(msg)
                return [Incremented(amount=a)]
            case Decrement(amount=a):
                if state.value - a < 0:
                    msg = 'Cannot go negative'
                    raise ValueError(msg)
                return [Decremented(amount=a)]
            case Noop():
                return []

    def evolve(  # noqa: PLR6301
        self,
        state: CounterState,
        event: Incremented | Decremented,
    ) -> CounterState:
        match event:
            case Incremented(amount=a):
                return CounterState(value=state.value + a)
            case Decremented(amount=a):
                return CounterState(value=state.value - a)


CounterSpec: TypeAlias = DeciderSpec[CounterState, Increment | Decrement | Noop, Incremented | Decremented]


@pytest.fixture
def spec() -> CounterSpec:
    return DeciderSpec.for_(CounterDecider())


def test_for_creates_spec() -> None:
    result = DeciderSpec.for_(CounterDecider())

    assert isinstance(result, DeciderSpec)


def test_given_empty_when_command_then_produces_events(spec: CounterSpec) -> None:
    spec.given([]).when(Increment(amount=3)).then([Incremented(amount=3)])


def test_given_history_when_command_then_produces_events(spec: CounterSpec) -> None:
    spec.given([Incremented(amount=5)]).when(Decrement(amount=3)).then([Decremented(amount=3)])


def test_when_invalid_command_then_raises(spec: CounterSpec) -> None:
    spec.when(Increment(amount=0)).then_raises(ValueError)


def test_given_history_when_invalid_command_then_raises_with_match(spec: CounterSpec) -> None:
    spec.given([Incremented(amount=2)]).when(Decrement(amount=5)).then_raises(
        ValueError,
        match='Cannot go negative',
    )


def test_when_noop_command_then_no_events(spec: CounterSpec) -> None:
    spec.when(Noop()).then_no_events()


def test_given_events_then_state_without_command(spec: CounterSpec) -> None:
    spec.given([Incremented(amount=3), Incremented(amount=7)]).then_state(lambda s: s.value == 10)


def test_given_empty_when_command_then_state(spec: CounterSpec) -> None:
    spec.given([]).when(Increment(amount=5)).then_state(lambda s: s.value == 5)


def test_then_with_wrong_events_raises_assertion_error(spec: CounterSpec) -> None:
    with pytest.raises(AssertionError):
        spec.given([]).when(Increment(amount=3)).then([Incremented(amount=999)])


def test_then_state_with_failing_predicate_raises_assertion_error(spec: CounterSpec) -> None:
    with pytest.raises(AssertionError):
        spec.given([Incremented(amount=1)]).then_state(lambda s: s.value == 999)
