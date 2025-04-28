from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NewType, Protocol

import pytest

from waku import WakuApplication, WakuFactory
from waku.di import Scope, contextual, scoped
from waku.ext.validation import ValidationExtension, ValidationRule
from waku.ext.validation.rules import DependenciesAccessible
from waku.modules import ModuleType, module


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


def _impl() -> int:  # pragma: no cover
    return 1


@pytest.fixture
def rule() -> ValidationRule:
    return DependenciesAccessible()


class ApplicationFactoryFunc(Protocol):
    def __call__(self, root_module: ModuleType) -> WakuApplication: ...


@pytest.fixture
def application_factory(rule: ValidationRule) -> ApplicationFactoryFunc:
    def factory(root_module: ModuleType) -> WakuApplication:
        return WakuFactory(
            root_module,
            extensions=[ValidationExtension([rule])],
        ).create()

    return factory


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
    application_factory: ApplicationFactoryFunc,
) -> None:
    @module(providers=[scoped(A)], exports=[A] if exports else [])
    class AModule:
        pass

    @module(providers=[scoped(B)], imports=[AModule] if imports else [])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = application_factory(AppModule)

    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    error = exc_info.value.exceptions[0].exceptions[0]
    b_module = application.registry.get(BModule)
    error_message = f'"{B!r}" from "{b_module!r}" depends on "{A!r}" but it\'s not accessible to it'
    assert str(error).startswith(error_message)

    application = WakuFactory(
        AppModule,
        extensions=[ValidationExtension([rule], strict=False)],
    ).create()
    with pytest.warns(Warning, match=re.escape(error_message)):
        await application.initialize()


async def test_ok(application_factory: ApplicationFactoryFunc) -> None:
    @module(providers=[scoped(A), scoped(_impl, provided_type=C)], exports=[A, C])
    class AModule:
        pass

    @module(providers=[scoped(B), scoped(D)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = application_factory(AppModule)
    await application.initialize()


async def test_ok_with_global_providers(application_factory: ApplicationFactoryFunc) -> None:
    @module(providers=[scoped(A)], is_global=True)
    class AModule:
        pass

    @module(providers=[scoped(B)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = application_factory(AppModule)
    await application.initialize()


@pytest.mark.parametrize('scope', [Scope.APP, Scope.REQUEST])
async def test_with_contextual_provider(rule: ValidationRule, scope: Scope) -> None:
    @module(
        providers=[
            contextual(A, scope=scope),
            scoped(B),
        ],
        exports=[B],
    )
    class Module:
        pass

    @module(imports=[Module])
    class AppModule:
        pass

    application = WakuFactory(
        AppModule,
        context={A: A()},
        extensions=[ValidationExtension([rule])],
    ).create()

    await application.initialize()


async def test_ok_with_application_providers(application_factory: ApplicationFactoryFunc) -> None:
    @module(providers=[scoped(B)], exports=[B])
    class BModule:
        pass

    @module(providers=[scoped(A)], imports=[BModule])
    class AppModule:
        pass

    application = application_factory(AppModule)
    await application.initialize()
