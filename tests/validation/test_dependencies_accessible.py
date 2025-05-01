"""Tests for dependency accessibility validation."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, cast

import pytest
from dishka.exceptions import ImplicitOverrideDetectedError

from tests.data import A, AAliasType, B, C, DependentService, Service, X, Y, Z
from tests.module_utils import create_basic_module
from waku import WakuApplication, WakuFactory
from waku.di import AnyOf, Provider, Scope, contextual, provide, scoped, singleton
from waku.ext.validation import ValidationExtension, ValidationRule
from waku.ext.validation.rules import DependenciesAccessibleRule, DependencyInaccessibleError
from waku.modules import ModuleType


def _impl() -> int:  # pragma: no cover
    return 1


@pytest.fixture
def rule() -> ValidationRule:
    return DependenciesAccessibleRule()


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
    """Create a factory function for creating WakuApplication instances with validation."""

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
    AModule = create_basic_module(
        providers=[scoped(A)],
        exports=[A] if exports else [],
        name='AModule',
    )
    BModule = create_basic_module(
        providers=[scoped(B)],
        imports=[AModule] if imports else [],
        name='BModule',
    )
    AppModule = create_basic_module(
        imports=[AModule, BModule],
        name='AppModule',
    )

    application = application_factory(AppModule)

    if should_fail:
        with pytest.raises(ExceptionGroup) as exc_info:
            await application.initialize()
        b_module = application.registry.get(BModule)

        error = cast(DependencyInaccessibleError, exc_info.value.exceptions[0].exceptions[0])

        expected_error = DependencyInaccessibleError(A, B, b_module)
        assert isinstance(error, type(expected_error))
        assert str(error) == str(expected_error)
    else:
        await application.initialize()


async def test_accessible_with_exported_and_imported(application_factory: ApplicationFactoryFunc) -> None:
    """Test that dependency is accessible when exported and imported."""
    AModule = create_basic_module(
        providers=[scoped(A), scoped(_impl, provided_type=C)],
        exports=[A, C],
        name='AModule',
    )
    BModule = create_basic_module(
        providers=[scoped(B)],
        imports=[AModule],
        name='BModule',
    )
    AppModule = create_basic_module(
        imports=[AModule, BModule],
        name='AppModule',
    )

    await application_factory(AppModule).initialize()


async def test_accessible_with_global_provider(application_factory: ApplicationFactoryFunc) -> None:
    """Test that global providers are accessible everywhere."""
    AModule = create_basic_module(
        providers=[scoped(A)],
        exports=[A],
        name='AModule',
        is_global=True,
    )
    BModule = create_basic_module(
        providers=[scoped(B)],
        imports=[AModule],
        name='BModule',
    )
    AppModule = create_basic_module(
        imports=[AModule, BModule],
        name='AppModule',
    )

    await application_factory(AppModule).initialize()


@pytest.mark.parametrize('scope', [Scope.APP, Scope.REQUEST])
async def test_accessible_with_contextual_provider(
    rule: ValidationRule,
    scope: Scope,
) -> None:
    """Test that contextual providers are accessible if present in context."""
    Module = create_basic_module(
        providers=[
            contextual(A, scope=scope),
            scoped(B),
        ],
        exports=[B],
        name='Module',
    )
    AppModule = create_basic_module(
        imports=[Module],
        name='AppModule',
    )

    application = WakuFactory(
        AppModule,
        context={A: A()},
        extensions=[ValidationExtension([rule])],
    ).create()
    await application.initialize()


async def test_accessible_with_application_providers(application_factory: ApplicationFactoryFunc) -> None:
    """Test that providers in the application module are accessible."""
    BModule = create_basic_module(
        providers=[scoped(B)],
        exports=[B],
        name='BModule',
    )
    AppModule = create_basic_module(
        providers=[scoped(A)],
        imports=[BModule],
        exports=[A],
        name='AppModule',
    )

    application: WakuApplication = application_factory(AppModule)
    await application.initialize()


async def test_intra_module_access(application_factory: ApplicationFactoryFunc) -> None:
    """Test that providers can access each other within the same module without export."""
    Module = create_basic_module(
        providers=[scoped(A), scoped(B)],
        name='Module',
    )
    AppModule = create_basic_module(
        imports=[Module],
        name='AppModule',
    )

    await application_factory(AppModule).initialize()


async def test_multiple_missing_dependencies(application_factory: ApplicationFactoryFunc) -> None:
    """Test that multiple missing dependencies are all reported."""
    XYModule = create_basic_module(
        providers=[scoped(X), scoped(Y)],
        name='XYModule',
    )
    ZModule = create_basic_module(
        providers=[scoped(Z)],
        name='ZModule',
    )
    AppModule = create_basic_module(
        imports=[XYModule, ZModule],
        name='AppModule',
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await application_factory(AppModule).initialize()

    errors = cast(list[DependencyInaccessibleError], exc_info.value.exceptions[0].exceptions)
    assert errors[0].required_type is X
    assert errors[1].required_type is Y


async def test_warning_mode(application_factory: ApplicationFactoryFunc) -> None:
    """Test that non-strict mode emits warnings instead of raising."""
    AModule = create_basic_module(
        providers=[scoped(A)],
        name='AModule',
    )
    BModule = create_basic_module(
        providers=[scoped(B)],
        name='BModule',
    )
    AppModule = create_basic_module(
        imports=[AModule, BModule],
        name='AppModule',
    )

    application = application_factory(AppModule, strict=False)

    b_module = application.registry.get(BModule)
    expected_error = DependencyInaccessibleError(A, B, b_module)

    with pytest.warns(Warning, match=re.escape(str(expected_error))):
        await application.initialize()


async def test_any_of_provider(application_factory: ApplicationFactoryFunc) -> None:
    """Test that AnyOf provider works."""

    class AProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def provide_a(self) -> AnyOf[A, AAliasType]:  # noqa: PLR6301
            return A()

    @dataclass()
    class DependsOnAlias:
        a: AAliasType

    AModule = create_basic_module(
        providers=[AProvider()],
        exports=[A, AAliasType],
        name='AModule',
        is_global=True,
    )
    BModule = create_basic_module(
        providers=[scoped(DependsOnAlias)],
        name='BModule',
    )
    AppModule = create_basic_module(
        imports=[AModule, BModule],
        name='AppModule',
    )

    application = application_factory(AppModule)

    async with application, application.container() as request_container:
        a = await request_container.get(A)
        a_alias = await request_container.get(AAliasType)
        assert a is a_alias


async def test_module_cannot_reexport_imported_types(application_factory: ApplicationFactoryFunc) -> None:
    """Test that modules cannot re-export types they don't directly provide.

    This test verifies that a module cannot re-export types that it imports from other modules.
    This enforces proper module encapsulation by preventing transitive dependency access.

    The test should fail with a DependencyInaccessibleError when a module tries to:
        1. Import a type from another module
        2. Re-export that same type
        3. Have other modules depend on that re-exported type
    """
    SharedModule = create_basic_module(
        providers=[scoped(A)],
        exports=[A],
        name='SharedModule',
    )
    ReexportModule = create_basic_module(
        imports=[SharedModule],
        exports=[A],
        name='ReexportModule',
    )
    ConsumerModule = create_basic_module(
        providers=[scoped(B)],
        imports=[ReexportModule],
        name='ConsumerModule',
    )
    AppModule = create_basic_module(
        imports=[ConsumerModule],
        name='AppModule',
    )

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    error = cast(DependencyInaccessibleError, exc_info.value.exceptions[0].exceptions[0])
    expected_error = DependencyInaccessibleError(
        required_type=A,
        required_by=B,
        from_module=application.registry.get(ConsumerModule),
    )
    assert str(error) == str(expected_error)


async def test_reexported_module_dependencies(application_factory: ApplicationFactoryFunc) -> None:
    """Test that re-exported module dependencies are accessible in the consumer module."""
    SharedModule = create_basic_module(
        providers=[scoped(A)],
        exports=[A],
        name='SharedModule',
    )
    ReexportModule = create_basic_module(
        providers=[],
        imports=[SharedModule],
        exports=[SharedModule],
        name='ReexportModule',
    )
    # B depends on A, which is re-exported from SharedModule
    ConsumerModule = create_basic_module(
        providers=[scoped(B)],
        imports=[ReexportModule],
        name='ConsumerModule',
    )
    AppModule = create_basic_module(
        imports=[ConsumerModule],
        name='AppModule',
    )

    await application_factory(AppModule).initialize()


async def test_hierarchical_dependencies(application_factory: ApplicationFactoryFunc) -> None:
    """Test deep hierarchical dependencies between modules."""

    @dataclass
    class ServiceA:
        pass

    @dataclass
    class ServiceB:
        a: ServiceA

    @dataclass
    class ServiceC:
        b: ServiceB

    @dataclass
    class ServiceD:
        c: ServiceC

    ModuleA = create_basic_module(
        providers=[scoped(ServiceA)],
        exports=[ServiceA],
        name='ModuleA',
    )
    ModuleB = create_basic_module(
        providers=[scoped(ServiceB)],
        exports=[ServiceB],
        imports=[ModuleA],
        name='ModuleB',
    )
    ModuleC = create_basic_module(
        providers=[scoped(ServiceC)],
        exports=[ServiceC],
        imports=[ModuleB],
        name='ModuleC',
    )
    ModuleD = create_basic_module(
        providers=[scoped(ServiceD)],
        imports=[ModuleC],
        name='ModuleD',
    )
    AppModule = create_basic_module(
        imports=[ModuleD],
        name='AppModule',
    )

    await application_factory(AppModule).initialize()


async def test_transitive_dependencies_not_accessible(application_factory: ApplicationFactoryFunc) -> None:
    """Test that transitive dependencies are not accessible unless re-exported."""

    @dataclass
    class ServiceA:
        pass

    @dataclass
    class ServiceB:
        a: ServiceA

    @dataclass
    class ServiceC:
        # Depends on A which should not be accessible through transitive import
        a: ServiceA

    ModuleA = create_basic_module(
        providers=[scoped(ServiceA)],
        exports=[ServiceA],
        name='ModuleA',
    )
    ModuleB = create_basic_module(
        providers=[scoped(ServiceB)],
        exports=[ServiceB],
        imports=[ModuleA],
        name='ModuleB',
    )
    ModuleC = create_basic_module(
        providers=[scoped(ServiceC)],
        imports=[ModuleB],
        name='ModuleC',
    )
    AppModule = create_basic_module(
        imports=[ModuleC],
        name='AppModule',
    )

    with pytest.raises(ExceptionGroup) as exc_info:
        await application_factory(AppModule).initialize()

    error = cast(DependencyInaccessibleError, exc_info.value.exceptions[0].exceptions[0])
    assert error.required_type is ServiceA


async def test_multiple_module_providers(application_factory: ApplicationFactoryFunc) -> None:
    """Test when multiple modules provide the same type."""

    @dataclass
    class SharedInterface(ABC):
        @abstractmethod
        def get_name(self) -> str: ...

    @dataclass
    class FirstImplementation(SharedInterface):
        def get_name(self) -> str:  # pragma: no cover # noqa: PLR6301
            return 'first'

    @dataclass
    class SecondImplementation(SharedInterface):
        def get_name(self) -> str:  # pragma: no cover  # noqa: PLR6301
            return 'second'

    @dataclass
    class Consumer:
        dependency: SharedInterface

    FirstModule = create_basic_module(
        providers=[scoped(FirstImplementation, provided_type=SharedInterface)],
        exports=[SharedInterface],
        name='FirstModule',
    )
    SecondModule = create_basic_module(
        providers=[scoped(SecondImplementation, provided_type=SharedInterface)],
        exports=[SharedInterface],
        name='SecondModule',
    )
    AppModule = create_basic_module(
        providers=[scoped(Consumer)],
        imports=[FirstModule, SecondModule],
        name='AppModule',
    )

    with pytest.raises(ImplicitOverrideDetectedError):
        await application_factory(AppModule).initialize()


async def test_dependencies_from_indirect_imports_are_not_accessible(
    application_factory: ApplicationFactoryFunc,
) -> None:
    """Test that modules cannot access dependencies from indirect imports.

    This test verifies that a module can only access dependencies from modules it directly imports.
    Dependencies from modules that are imported by the module's imports (transitive dependencies)
    should not be accessible. This enforces explicit dependency declarations and prevents
    implicit dependency chains.

    Test structure:
    SecondLevelModule -> provides and exports Service
    FirstLevelModule  -> imports SecondLevelModule but doesn't re-export it
    ConsumerModule    -> imports FirstLevelModule and depends on Service

    Expected: DependencyInaccessibleError because ConsumerModule cannot access Service
             through FirstLevelModule's import of SecondLevelModule.
    """
    SecondLevelModule = create_basic_module(
        providers=[scoped(Service)],
        exports=[Service],
        name='SecondLevelModule',
    )
    FirstLevelModule = create_basic_module(
        imports=[SecondLevelModule],
        exports=[],
        name='FirstLevelModule',
    )
    ConsumerModule = create_basic_module(
        providers=[scoped(DependentService)],
        imports=[FirstLevelModule],
        name='ConsumerModule',
    )

    AppModule = create_basic_module(
        imports=[ConsumerModule],
        name='AppModule',
    )

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    error = cast(DependencyInaccessibleError, exc_info.value.exceptions[0].exceptions[0])
    expected_error = DependencyInaccessibleError(
        required_type=Service,
        required_by=DependentService,
        from_module=application.registry.get(ConsumerModule),
    )
    assert str(error) == str(expected_error)


async def test_with_realistic_graph(application_factory: ApplicationFactoryFunc) -> None:
    @dataclass
    class AsyncEngine:
        pass

    @dataclass
    class AsyncConnection:
        engine: AsyncEngine

    @dataclass
    class AsyncSession:
        connection: AsyncConnection

    DBModule = create_basic_module(
        providers=[
            singleton(AsyncEngine),
            scoped(AsyncConnection),
            scoped(AsyncSession),
        ],
        exports=[
            AsyncEngine,
            AsyncConnection,
            AsyncSession,
        ],
        name='DBModule',
    )

    InfraModule = create_basic_module(
        imports=[DBModule],
        exports=[DBModule],
        is_global=True,
        name='InfraModule',
    )

    @dataclass
    class Settings:
        pass

    @dataclass
    class UserService:
        session: AsyncSession
        settings: Settings

    UsersModule = create_basic_module(providers=[scoped(UserService)], name='UsersModule')

    AppModule = create_basic_module(
        providers=[contextual(Settings, scope=Scope.APP)],
        imports=[InfraModule, UsersModule],
        name='AppModule',
    )

    application = application_factory(AppModule, context={Settings: Settings()})
    await application.initialize()
