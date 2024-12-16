from __future__ import annotations

import re

import pytest

from lattice.application import Lattice
from lattice.di import Scoped
from lattice.modules import Module
from lattice.validation import ModuleValidationError, ValidationExtension
from tests.mock import DummyDI


class A:
    pass


class B:
    def __init__(self, a: A) -> None:
        self._a = a


@pytest.mark.parametrize(
    ('imports', 'exports'),
    [
        (False, False),
        (False, True),
        (True, False),
    ],
)
def test_inaccessible(imports: bool, exports: bool) -> None:
    a = Module(name='A', providers=[Scoped(A)], exports=[A] if exports else [])
    b = Module(name='B', providers=[Scoped(B)], imports=[a] if imports else [])

    error_message = f"{b!r} depends on {A!r} but it's not accessible to it"

    with pytest.raises(ModuleValidationError) as exc_info:
        Lattice(
            modules=[a, b],
            extensions=[ValidationExtension()],
            name='app',
            dependency_provider=DummyDI(),
        )
    assert str(exc_info.value) == error_message

    with pytest.warns(Warning, match=re.escape(error_message)):
        Lattice(
            modules=[a, b],
            extensions=[ValidationExtension(strict=False)],
            name='app',
            dependency_provider=DummyDI(),
        )


def test_ok() -> None:
    a = Module(name='A', providers=[Scoped(A)], exports=[A])
    b = Module(name='B', providers=[Scoped(B)], imports=[a])
    Lattice(modules=[a, b], extensions=[ValidationExtension()], name='app', dependency_provider=DummyDI())
