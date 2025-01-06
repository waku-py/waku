from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NewType

import pytest

from tests.mock import DummyDI
from waku.application import Application
from waku.di import Scoped
from waku.ext.validation import ValidationExtension, ValidationRule
from waku.ext.validation.rules import DependenciesAccessible
from waku.module import Module


@dataclass
class A:
    pass


@dataclass
class B:
    a: A


C = NewType('C', A)


@dataclass
class D:
    c: C


def _impl() -> int:
    return 1


@pytest.fixture
def rule() -> ValidationRule:
    return DependenciesAccessible()


@pytest.mark.parametrize(
    ('imports', 'exports'),
    [
        (False, False),
        (False, True),
        (True, False),
    ],
)
def test_inaccessible(
    imports: bool,
    exports: bool,
    rule: ValidationRule,
) -> None:
    a = Module(name='A', providers=[Scoped(A)], exports=[A] if exports else [])
    b = Module(name='B', providers=[Scoped(B)], imports=[a] if imports else [])

    error_message = f"{b!r} depends on {A!r} but it's not accessible to it"

    with pytest.raises(ExceptionGroup) as exc_info:
        Application(
            'app',
            modules=[a, b],
            dependency_provider=DummyDI(),
            extensions=[ValidationExtension([rule])],
        )

    error = exc_info.value.exceptions[0]
    assert str(error) == error_message

    with pytest.warns(Warning, match=re.escape(error_message)):
        Application(
            name='app',
            modules=[a, b],
            dependency_provider=DummyDI(),
            extensions=[ValidationExtension([rule], strict=False)],
        )


def test_ok(rule: ValidationRule) -> None:
    a = Module(name='A', providers=[Scoped(A), Scoped(_impl, C)], exports=[A, C])
    b = Module(name='B', providers=[Scoped(B), Scoped(D)], imports=[a])
    Application(
        'app',
        modules=[a, b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )


def test_ok_with_global_providers(rule: ValidationRule) -> None:
    a = Module(name='A', providers=[Scoped(A)], is_global=True)
    b = Module(name='B', providers=[Scoped(B)])
    Application(
        'app',
        modules=[a, b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )


def test_ok_with_application_providers(rule: ValidationRule) -> None:
    b = Module(name='B', providers=[Scoped(B)], exports=[B])
    Application(
        'app',
        modules=[b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
        providers=[Scoped(A)],
    )
