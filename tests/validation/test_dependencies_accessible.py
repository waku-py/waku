from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NewType

import pytest

from tests.mock import DummyDI
from waku import WakuFactory
from waku.di import Scoped
from waku.ext.validation import ValidationExtension, ValidationRule
from waku.ext.validation.rules import DependenciesAccessible
from waku.modules import module


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
async def test_inaccessible(
    imports: bool,
    exports: bool,
    rule: ValidationRule,
) -> None:
    b_provider = Scoped(B)

    @module(providers=[Scoped(A)], exports=[A] if exports else [])
    class AModule:
        pass

    @module(providers=[b_provider], imports=[AModule] if imports else [])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    error = exc_info.value.exceptions[0].exceptions[0]
    b_module = application.container.get_module(BModule)
    error_message = f'Provider "{b_provider!r}" from "{b_module!r}" depends on "{A!r}" but it\'s not accessible to it'
    assert str(error).startswith(error_message)

    application = WakuFactory.create(
        AppModule,
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule], strict=False)],
    )
    with pytest.warns(Warning, match=re.escape(error_message)):
        await application.initialize()


async def test_ok(rule: ValidationRule) -> None:
    @module(providers=[Scoped(A), Scoped(_impl, C)], exports=[A, C])
    class AModule:
        pass

    @module(providers=[Scoped(B), Scoped(D)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )
    await application.initialize()


async def test_ok_with_global_providers(rule: ValidationRule) -> None:
    @module(providers=[Scoped(A)], is_global=True)
    class AModule:
        pass

    @module(providers=[Scoped(B)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )
    await application.initialize()


async def test_ok_with_application_providers(rule: ValidationRule) -> None:
    @module(providers=[Scoped(B)], exports=[B])
    class BModule:
        pass

    @module(providers=[Scoped(A)], imports=[BModule])
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=DummyDI(),
        extensions=[ValidationExtension([rule])],
    )
    await application.initialize()
