from __future__ import annotations

import re
from typing import NewType

import pytest

from tests.mock import DummyDI
from waku.application import Application
from waku.di import Scoped
from waku.ext.validation import ValidationError, ValidationExtension, ValidationRule
from waku.module import Module


class A:
    pass


class B:
    def __init__(self, a: A) -> None:
        self._a = a


C = NewType('C', A)


class D:
    def __init__(self, c: C) -> None:
        self._c = c


def _impl() -> int:
    return 1


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
    rules: list[ValidationRule],
) -> None:
    a = Module(name='A', providers=[Scoped(A)], exports=[A] if exports else [])
    b = Module(name='B', providers=[Scoped(B)], imports=[a] if imports else [])

    error_message = f"{b!r} depends on {A!r} but it's not accessible to it"

    with pytest.raises(ValidationError) as exc_info:
        Application(
            'app',
            modules=[a, b],
            dependency_provider=DummyDI(),
            extensions=[ValidationExtension(rules)],
        )
    assert str(exc_info.value) == error_message

    with pytest.warns(Warning, match=re.escape(error_message)):
        Application(
            name='app',
            modules=[a, b],
            dependency_provider=DummyDI(),
            extensions=[ValidationExtension(rules, strict=False)],
        )


def test_ok(rules: list[ValidationRule]) -> None:
    a = Module(name='A', providers=[Scoped(A), Scoped(_impl, C)], exports=[A, C])
    b = Module(name='B', providers=[Scoped(B), Scoped(D)], imports=[a])
    Application(
        'app',
        modules=[a, b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension(rules)],
    )


def test_ok_with_global_providers(rules: list[ValidationRule]) -> None:
    a = Module(name='A', providers=[Scoped(A)], is_global=True)
    b = Module(name='B', providers=[Scoped(B)])
    Application(
        'app',
        modules=[a, b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension(rules)],
    )


def test_ok_with_application_providers(rules: list[ValidationRule]) -> None:
    b = Module(name='B', providers=[Scoped(B)], exports=[B])
    Application(
        'app',
        modules=[b],
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension(rules)],
        providers=[Scoped(A)],
    )
