from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.testing import AggregateSpec


@dataclass(frozen=True)
class Incremented(INotification):
    amount: int


@dataclass(frozen=True)
class Decremented(INotification):
    amount: int


class CounterAggregate(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.value: int = 0

    def increment(self, amount: int) -> None:
        if amount <= 0:
            msg = 'Amount must be positive'
            raise ValueError(msg)
        self._raise_event(Incremented(amount=amount))

    def decrement(self, amount: int) -> None:
        if self.value - amount < 0:
            msg = 'Cannot go negative'
            raise ValueError(msg)
        self._raise_event(Decremented(amount=amount))

    def noop(self) -> None:
        pass

    def _apply(self, event: INotification) -> None:
        match event:
            case Incremented(amount=a):
                self.value += a
            case Decremented(amount=a):
                self.value -= a


@pytest.fixture
def spec() -> AggregateSpec[CounterAggregate]:
    return AggregateSpec.for_(CounterAggregate)


def test_for_creates_spec() -> None:
    result = AggregateSpec.for_(CounterAggregate)
    assert isinstance(result, AggregateSpec)


def test_given_empty_when_action_then_produces_events(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.given([]).when(lambda agg: agg.increment(3)).then([Incremented(amount=3)])


def test_given_history_when_action_then_produces_events(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.given([Incremented(amount=5)]).when(lambda agg: agg.decrement(3)).then([Decremented(amount=3)])


def test_when_invalid_action_then_raises(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.when(lambda agg: agg.increment(0)).then_raises(ValueError)


def test_given_history_when_invalid_action_then_raises_with_match(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.given([Incremented(amount=2)]).when(lambda agg: agg.decrement(5)).then_raises(
        ValueError,
        match='Cannot go negative',
    )


def test_when_noop_action_then_no_events(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.when(lambda agg: agg.noop()).then_no_events()


def test_given_events_then_state_without_action(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.given([Incremented(amount=3), Incremented(amount=7)]).then_state(lambda agg: agg.value == 10)


def test_given_empty_when_action_then_state(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.given([]).when(lambda agg: agg.increment(5)).then_state(lambda agg: agg.value == 5)


def test_then_with_wrong_events_raises_assertion_error(spec: AggregateSpec[CounterAggregate]) -> None:
    with pytest.raises(AssertionError):
        spec.given([]).when(lambda agg: agg.increment(3)).then([Incremented(amount=999)])


def test_then_state_with_failing_predicate_raises_assertion_error(spec: AggregateSpec[CounterAggregate]) -> None:
    with pytest.raises(AssertionError):
        spec.given([Incremented(amount=1)]).then_state(lambda agg: agg.value == 999)


def test_given_no_history_creates_fresh_aggregate(spec: AggregateSpec[CounterAggregate]) -> None:
    spec.then_state(lambda agg: agg.value == 0 and agg.version == -1)
