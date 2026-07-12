from __future__ import annotations

import sys
import typing
from dataclasses import dataclass
from typing import Generic, TypeVar

import pytest
from typing_extensions import TypeAliasType

from waku.eventsourcing._internal.introspection import resolve_generic_args
from waku.eventsourcing.decider.repository import DeciderRepository
from waku.messages import IEvent

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
class Event(IEvent):
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


# A native `type X = ...` yields a stdlib ``typing.TypeAliasType`` (distinct from the te backport on
# te >= 4.16); resolve the constructor dynamically to keep this module importable on 3.11, where
# ``typing.TypeAliasType`` does not exist and native ``type`` syntax is a SyntaxError.
_native_type_alias_factory: typing.Any = vars(typing).get('TypeAliasType')


@pytest.mark.skipif(sys.version_info < (3, 12), reason='PEP 695 type aliases require Python 3.12+')
def test_native_type_alias_state_infers_aggregate_name() -> None:
    native_aliased_state = _native_type_alias_factory('AliasedState', StateA | StateB)

    class _NativeAliasRepo(DeciderRepository[native_aliased_state, Command, Event]):  # type: ignore[valid-type]
        pass

    assert _NativeAliasRepo.aggregate_name == 'Aliased'


def test_unparameterized_repository_raises() -> None:
    with pytest.raises(TypeError, match='must define aggregate_name or parametrize Generic'):

        class _BareRepo(DeciderRepository):  # type: ignore[type-arg]
            pass


def test_plain_state_without_state_suffix_uses_full_name() -> None:
    class _Repo(DeciderRepository[StateA, Command, Event]):
        pass

    assert _Repo.aggregate_name == 'StateA'
