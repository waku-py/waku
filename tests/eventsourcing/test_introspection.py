from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import pytest
from typing_extensions import TypeAliasType

from waku.eventsourcing._introspection import resolve_generic_args  # noqa: PLC2701
from waku.eventsourcing.decider.repository import DeciderRepository

S = TypeVar('S')
C = TypeVar('C')
E = TypeVar('E')


class Base(Generic[S, C, E]):
    pass


@dataclass(frozen=True)
class StateA:
    pass


@dataclass(frozen=True)
class StateB:
    pass


@dataclass(frozen=True)
class Command:
    pass


@dataclass(frozen=True)
class Event:
    pass


AliasedState = TypeAliasType('AliasedState', StateA | StateB)


class WithPlainType(Base[StateA, Command, Event]):
    pass


class WithUnionType(Base[StateA | StateB, Command, Event]):
    pass


class WithTypeAlias(Base[AliasedState, Command, Event]):
    pass


class WithUnboundGenerics(Base[S, C, E]):
    pass


class WithPartiallyBoundGenerics(Base[S, Command, Event]):
    pass


def test_resolves_plain_type_args() -> None:
    args = resolve_generic_args(WithPlainType, Base)

    assert args == (StateA, Command, Event)


def test_resolves_union_type_args() -> None:
    args = resolve_generic_args(WithUnionType, Base)

    assert args is not None
    assert args[0] == StateA | StateB
    assert args[1] is Command
    assert args[2] is Event


def test_resolves_type_alias_args() -> None:
    args = resolve_generic_args(WithTypeAlias, Base)

    assert args is not None
    assert args[0] is AliasedState
    assert args[1] is Command
    assert args[2] is Event


def test_returns_none_for_unbound_generics() -> None:
    args = resolve_generic_args(WithUnboundGenerics, Base)

    assert args is None


def test_returns_none_for_partially_bound_generics() -> None:
    args = resolve_generic_args(WithPartiallyBoundGenerics, Base)

    assert args is None


def test_union_state_without_aggregate_name_raises() -> None:
    with pytest.raises(TypeError, match=r'cannot infer aggregate_name.*Union or complex state types'):

        class _BadRepo(DeciderRepository[StateA | StateB, Command, Event]):
            pass


def test_union_state_with_explicit_aggregate_name_works() -> None:
    class _GoodRepo(DeciderRepository[StateA | StateB, Command, Event]):
        aggregate_name = 'Case'

    assert _GoodRepo.aggregate_name == 'Case'


def test_type_alias_state_infers_aggregate_name() -> None:
    class _AliasRepo(DeciderRepository[AliasedState, Command, Event]):
        pass

    assert _AliasRepo.aggregate_name == 'Aliased'


def test_unparameterized_repository_raises() -> None:
    with pytest.raises(TypeError, match='must define aggregate_name or parametrize Generic'):

        class _BareRepo(DeciderRepository):  # type: ignore[type-arg]
            pass


def test_plain_state_without_state_suffix_uses_full_name() -> None:
    class _Repo(DeciderRepository[StateA, Command, Event]):
        pass

    assert _Repo.aggregate_name == 'StateA'
