from dataclasses import dataclass
from typing import Protocol, TypeVar

import pytest

from waku import WakuApplication, WakuFactory
from waku.di import AnyOf, Provider, Scope, contextual, provide, scoped, singleton
from waku.modules import Module, ModuleType
from waku.validation import ValidationExtension, ValidationRule
from waku.validation.rules import DependenciesAccessibleRule, DependencyInaccessibleError

from tests.data import A, AAliasType, B, DependentService, Service, X, Y, Z
from tests.module_utils import create_basic_module

_T_co = TypeVar('_T_co', covariant=True)


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


def assert_single_inaccessible_error(
    exc_info: pytest.ExceptionInfo[BaseException],
    required_type: type,
    required_by: type,
    from_module: Module,
) -> None:
    assert isinstance(exc_info.value, ExceptionGroup)
    errors = exc_info.value.exceptions
    assert len(errors) == 1
    error = errors[0]
    assert isinstance(error, DependencyInaccessibleError)
    assert error.required_type is required_type
    assert error.required_by is required_by
    assert error.from_module is from_module


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
        assert_single_inaccessible_error(exc_info, required_type=A, required_by=B, from_module=b_module)
    else:
        await application.initialize()


async def test_accessible_with_global_provider(application_factory: ApplicationFactoryFunc) -> None:
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

    assert isinstance(exc_info.value, ExceptionGroup)
    errors = exc_info.value.exceptions
    assert len(errors) == 2
    first_error = errors[0]
    second_error = errors[1]
    assert isinstance(first_error, DependencyInaccessibleError)
    assert isinstance(second_error, DependencyInaccessibleError)
    assert first_error.required_type is X
    assert second_error.required_type is Y


async def test_warning_mode(application_factory: ApplicationFactoryFunc) -> None:
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

    with pytest.warns(UserWarning, match=r'not accessible') as warning_records:
        await application.initialize()

    assert len(warning_records) == 1
    warning_message = str(warning_records[0].message)
    assert repr(A) in warning_message
    assert repr(B) in warning_message
    assert repr(b_module) in warning_message


async def test_any_of_provider(application_factory: ApplicationFactoryFunc) -> None:
    class AProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def provide_a(self) -> AnyOf[A, AAliasType]:  # noqa: PLR6301
            return A()  # pragma: no cover

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

    await application_factory(AppModule).initialize()


async def test_module_cannot_reexport_imported_types(application_factory: ApplicationFactoryFunc) -> None:
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

    consumer_module = application.registry.get(ConsumerModule)
    assert_single_inaccessible_error(exc_info, required_type=A, required_by=B, from_module=consumer_module)


async def test_reexported_module_dependencies(application_factory: ApplicationFactoryFunc) -> None:
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
    @dataclass
    class ServiceA:
        pass

    @dataclass
    class ServiceB:
        a: ServiceA

    @dataclass
    class ServiceC:
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

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    module_c = application.registry.get(ModuleC)
    assert_single_inaccessible_error(exc_info, required_type=ServiceA, required_by=ServiceC, from_module=module_c)


async def test_dependencies_from_indirect_imports_are_not_accessible(
    application_factory: ApplicationFactoryFunc,
) -> None:
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

    consumer_module = application.registry.get(ConsumerModule)
    assert_single_inaccessible_error(
        exc_info, required_type=Service, required_by=DependentService, from_module=consumer_module
    )


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


async def test_with_generic_provider(application_factory: ApplicationFactoryFunc) -> None:
    @dataclass
    class User:
        pass

    @dataclass
    class AdminUser(User):
        pass

    class IUserFactory(Protocol[_T_co]):
        def create(self) -> _T_co: ...

    class UserFactory(IUserFactory[User]):
        def create(self) -> User:  # noqa: PLR6301
            return User()  # pragma: no cover

    class AdminUserFactory(IUserFactory[AdminUser]):
        def create(self) -> AdminUser:  # noqa: PLR6301
            return AdminUser()  # pragma: no cover

    UsersModule = create_basic_module(
        providers=[
            scoped(AnyOf[IUserFactory[User], UserFactory], UserFactory),
            scoped(AnyOf[IUserFactory[AdminUser], AdminUserFactory], AdminUserFactory),
        ],
        name='UsersModule',
        exports=[
            IUserFactory[User],
            IUserFactory[AdminUser],
            UserFactory,
            AdminUserFactory,
        ],
    )

    @dataclass
    class FactoryService:
        user_factory: IUserFactory[User]
        admin_user_factory: IUserFactory[AdminUser]
        concrete_user_factory: UserFactory
        concrete_admin_user_factory: AdminUserFactory

    AppModule = create_basic_module(
        providers=[scoped(FactoryService)],
        imports=[UsersModule],
        name='AppModule',
    )

    application = application_factory(AppModule)
    await application.initialize()


async def test_global_module_reexports_generic_provider(application_factory: ApplicationFactoryFunc) -> None:
    @dataclass
    class Entity:
        pass

    class IRepository(Protocol[_T_co]):
        def get(self) -> _T_co: ...

    class EntityRepository(IRepository[Entity]):
        def get(self) -> Entity:  # noqa: PLR6301
            return Entity()  # pragma: no cover

    NonGlobalModule = create_basic_module(
        providers=[scoped(IRepository[Entity], EntityRepository)],
        exports=[IRepository[Entity]],
        name='NonGlobalModule',
        is_global=False,
    )

    GlobalModule = create_basic_module(
        imports=[NonGlobalModule],
        exports=[NonGlobalModule],
        name='GlobalModule',
        is_global=True,
    )

    @dataclass
    class ConsumerService:
        repository: IRepository[Entity]

    ConsumerModule = create_basic_module(
        providers=[scoped(ConsumerService)],
        name='ConsumerModule',
    )

    AppModule = create_basic_module(
        imports=[GlobalModule, ConsumerModule],
        name='AppModule',
    )

    application = application_factory(AppModule)
    await application.initialize()
