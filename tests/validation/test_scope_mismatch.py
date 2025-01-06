import dataclasses
from typing import Any

import pytest

from tests.mock import DummyDI
from waku import Application, Module
from waku.di import Object, Provider, Scoped, Singleton, Transient
from waku.ext.validation import ValidationExtension, ValidationRule
from waku.ext.validation.rules import DIScopeMismatch


class _A:
    pass


@dataclasses.dataclass
class _B:
    a: _A


@pytest.fixture
def rule() -> ValidationRule:
    return DIScopeMismatch()


@pytest.mark.parametrize(
    ('provider', 'dependency', 'should_error'),
    [
        (Scoped(_B), Scoped(_A), False),
        (Scoped(_B), Singleton(_A), False),
        (Singleton(_B), Singleton(_A), False),
        (Singleton(_B), Scoped(_A), True),
        (Singleton(_B), Transient(_A), True),
        # "Object" should already be instantiated
        (Object(_B(a=_A())), Scoped(_A), False),
        (Object(_B(a=_A())), Transient(_A), False),
        (Object(_B(a=_A())), Singleton(_A), False),
        (Scoped(_B), Object(_A()), False),
        (Transient(_B), Object(_A()), False),
        (Singleton(_B), Object(_A()), False),
    ],
)
def test_scope_mismatch(
    provider: Provider[Any],
    dependency: Provider[Any],
    should_error: bool,
    rule: ValidationRule,
) -> None:
    def create_app() -> None:
        Application(
            'app',
            modules=[Module(name='module', providers=[provider, dependency])],
            dependency_provider=DummyDI(),
            extensions=[ValidationExtension([rule])],
        )

    if not should_error:
        create_app()
        return

    with pytest.raises(ExceptionGroup) as exc_info:
        create_app()

    error = exc_info.value.exceptions[0]
    assert str(error).startswith(f'{provider!r} depends on {dependency!r}')
