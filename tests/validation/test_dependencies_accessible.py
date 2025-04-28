from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NewType, Protocol, cast

import pytest

from waku import WakuApplication, WakuFactory
from waku.di import Scope, contextual, scoped
from waku.ext.validation import ValidationError, ValidationExtension, ValidationRule
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
    def __call__(
        self,
        root_module: ModuleType,
        *,
        strict: bool = True,
        context: dict[type, object] | None = None,
    ) -> WakuApplication: ...


@pytest.fixture
def application_factory(rule: ValidationRule) -> ApplicationFactoryFunc:
    def factory(
        root_module: ModuleType,
        *,
        strict: bool = True,
        context: dict[type, object] | None = None,
    ) -> WakuApplication:
        return WakuFactory(
            root_module,
            context=context or {},
            extensions=[ValidationExtension([rule], strict=strict)],
        ).create()

    return factory


@pytest.mark.parametrize(
    ('imports', 'exports', 'should_fail'),
    [
        (False, False, True),
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
async def test_accessibility_import_export_matrix(
    imports: bool,
    exports: bool,
    should_fail: bool,
    application_factory: ApplicationFactoryFunc,
) -> None:
    """Test all combinations of import/export for dependency accessibility."""

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
    if should_fail:
        with pytest.raises(ExceptionGroup) as exc_info:
            await application.initialize()
        b_module = application.registry.get(BModule)
        error_message = f'"{B!r}" from "{b_module!r}" depends on "{A!r}" but it\'s not accessible to it'
        error = cast(ValidationError, exc_info.value.exceptions[0].exceptions[0])
        assert str(error).startswith(error_message)
    else:
        await application.initialize()


async def test_accessible_with_exported_and_imported(application_factory: ApplicationFactoryFunc) -> None:
    """Test that dependency is accessible when exported and imported."""

    @module(providers=[scoped(A), scoped(_impl, provided_type=C)], exports=[A, C])
    class AModule:
        pass

    @module(providers=[scoped(B), scoped(D)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    await application_factory(AppModule).initialize()


async def test_accessible_with_global_provider(application_factory: ApplicationFactoryFunc) -> None:
    """Test that global providers are accessible everywhere."""

    @module(providers=[scoped(A)], is_global=True)
    class AModule:
        pass

    @module(providers=[scoped(B)], imports=[AModule])
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    await application_factory(AppModule).initialize()


@pytest.mark.parametrize('scope', [Scope.APP, Scope.REQUEST])
async def test_accessible_with_contextual_provider(
    rule: ValidationRule,
    scope: Scope,
) -> None:
    """Test that contextual providers are accessible if present in context."""

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


async def test_accessible_with_application_providers(application_factory: ApplicationFactoryFunc) -> None:
    """Test that providers in the application module are accessible."""

    @module(providers=[scoped(B)], exports=[B])
    class BModule:
        pass

    @module(providers=[scoped(A)], imports=[BModule])
    class AppModule:
        pass

    application: WakuApplication = application_factory(AppModule)
    await application.initialize()


async def test_intra_module_access(application_factory: ApplicationFactoryFunc) -> None:
    """Test that providers can access each other within the same module without export."""

    @module(providers=[scoped(A), scoped(B)])
    class Module:
        pass

    @module(imports=[Module])
    class AppModule:
        pass

    await application_factory(AppModule).initialize()


@dataclass
class X:
    pass


@dataclass
class Y:
    pass


@dataclass
class Z:
    x: X
    y: Y


async def test_multiple_missing_dependencies(application_factory: ApplicationFactoryFunc) -> None:
    """Test that multiple missing dependencies are all reported."""

    @module(providers=[scoped(X), scoped(Y)])
    class XYModule:
        pass

    @module(providers=[scoped(Z)])
    class ZModule:
        pass

    @module(imports=[XYModule, ZModule])
    class AppModule:
        pass

    with pytest.raises(ExceptionGroup) as exc_info:
        await application_factory(AppModule).initialize()

    validation_errors = exc_info.value.exceptions[0].exceptions
    error_msgs = [str(e) for e in validation_errors]
    assert any('depends on' in msg and 'X' in msg for msg in error_msgs)
    assert any('depends on' in msg and 'Y' in msg for msg in error_msgs)


async def test_warning_mode(application_factory: ApplicationFactoryFunc) -> None:
    """Test that non-strict mode emits warnings instead of raising."""

    @module(providers=[scoped(A)])
    class AModule:
        pass

    @module(providers=[scoped(B)], imports=[])  # No import, should warn
    class BModule:
        pass

    @module(imports=[AModule, BModule])
    class AppModule:
        pass

    application = application_factory(AppModule, strict=False)

    b_module = application.registry.get(BModule)
    error_message = f'"{B!r}" from "{b_module!r}" depends on "{A!r}" but it\'s not accessible to it'
    with pytest.warns(Warning, match=re.escape(error_message)):
        await application.initialize()
