from dataclasses import dataclass
from typing import Annotated, Generic, Protocol, TypeVar, cast

import pytest
from typing_extensions import override

from waku import Module, WakuApplication, WakuFactory
from waku.di import (
    AnyOf,
    FromComponent,
    Has,
    Marker,
    Provider,
    Scope,
    activator,
    contextual,
    provide,
    scoped,
    singleton,
)
from waku.modules import ModuleType
from waku.validation import ValidationExtension, ValidationRule
from waku.validation.rules import DependenciesAccessibleRule, DependencyInaccessibleError

from tests.data import A, AAliasType, B, DependentService, Service, X, Y, Z
from tests.module_utils import create_basic_module

_T_co = TypeVar('_T_co', covariant=True)
_BoxT = TypeVar('_BoxT')


@dataclass
class _Secret:
    pass


class _SecretAlias:
    pass


@dataclass
class _DecoratedService:
    pass


class _MissingActivation:
    pass


@dataclass
class _DynamicActivation:
    enabled: bool = True


@dataclass
class _ActiveSecret:
    pass


@dataclass
class _InactiveSecret:
    pass


@dataclass
class _Box(Generic[_BoxT]):
    value: _BoxT


def _decorate_service(service: _DecoratedService, secret: _Secret) -> _DecoratedService:
    del secret
    return service


def _decorate_target_only(service: _DecoratedService) -> _DecoratedService:
    return service


def _make_int_box() -> _Box[int]:
    return _Box(1)


def _decorate_box(box: _Box[_BoxT], secret: _Secret) -> _Box[_BoxT]:
    del secret
    return box


def _dynamic_activation_enabled(config: _DynamicActivation) -> bool:
    return config.enabled


def _dynamic_activation() -> _DynamicActivation:
    return _DynamicActivation()


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
    from_module: Module,
    required_by: object | None = None,
) -> None:
    assert isinstance(exc_info.value, ExceptionGroup)
    errors = exc_info.value.exceptions
    assert len(errors) == 1
    error = errors[0]
    assert isinstance(error, DependencyInaccessibleError)
    assert error.required_type is required_type
    if required_by is not None:
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


async def test_alias_source_must_be_accessible(application_factory: ApplicationFactoryFunc) -> None:
    alias_provider = Provider()
    alias_provider.alias(_Secret, provides=_SecretAlias)
    PrivateModule = create_basic_module(
        providers=[scoped(_Secret)],
        name='PrivateModule',
    )
    AliasModule = create_basic_module(
        providers=[alias_provider],
        imports=[PrivateModule],
        exports=[_SecretAlias],
        name='AliasModule',
    )
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    alias_module = application.registry.get(AliasModule)
    assert_single_inaccessible_error(exc_info, required_type=_Secret, from_module=alias_module)


async def test_alias_source_can_be_exported(application_factory: ApplicationFactoryFunc) -> None:
    alias_provider = Provider()
    alias_provider.alias(_Secret, provides=_SecretAlias)
    SecretModule = create_basic_module(
        providers=[scoped(_Secret)],
        exports=[_Secret],
        name='SecretModule',
    )
    AliasModule = create_basic_module(
        providers=[alias_provider],
        imports=[SecretModule],
        name='AliasModule',
    )
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_alias_dependencies_are_not_validated(application_factory: ApplicationFactoryFunc) -> None:
    alias_provider = Provider()
    alias_provider.alias(_Secret, provides=_SecretAlias, when=Has(_MissingActivation))
    AliasModule = create_basic_module(providers=[alias_provider], name='AliasModule')
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_alias_does_not_hide_active_alias_dependency(
    application_factory: ApplicationFactoryFunc,
) -> None:
    alias_provider = Provider()
    alias_provider.alias(_ActiveSecret, provides=_SecretAlias, when=Has(_ActiveSecret))
    alias_provider.alias(_InactiveSecret, provides=_SecretAlias, when=Has(_MissingActivation))
    PrivateModule = create_basic_module(
        providers=[scoped(_ActiveSecret), scoped(_InactiveSecret)],
        name='PrivateModule',
    )
    AliasModule = create_basic_module(
        providers=[alias_provider],
        imports=[PrivateModule],
        name='AliasModule',
    )
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    alias_module = application.registry.get(AliasModule)
    assert_single_inaccessible_error(exc_info, required_type=_ActiveSecret, from_module=alias_module)


async def test_inactive_local_factory_does_not_authorize_active_alias_source(
    application_factory: ApplicationFactoryFunc,
) -> None:
    local_provider = Provider(scope=Scope.REQUEST)
    local_provider.provide(_Secret, when=Has(_MissingActivation))
    alias_provider = Provider()
    alias_provider.alias(_Secret, provides=_SecretAlias)
    PrivateModule = create_basic_module(providers=[scoped(_Secret)], name='PrivateModule')
    AliasModule = create_basic_module(
        providers=[local_provider, alias_provider],
        imports=[PrivateModule],
        name='AliasModule',
    )
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    alias_module = application.registry.get(AliasModule)
    assert_single_inaccessible_error(exc_info, required_type=_Secret, from_module=alias_module)


async def test_same_type_alias_source_component_must_be_accessible(
    application_factory: ApplicationFactoryFunc,
) -> None:
    private_provider = Provider(scope=Scope.REQUEST)
    private_provider.provide(
        _DecoratedService,
        provides=Annotated[_DecoratedService, FromComponent('private')],
    )
    alias_provider = Provider()
    alias_provider.alias(
        cast('type', Annotated[_DecoratedService, FromComponent('private')]),
        provides=_DecoratedService,
    )
    PrivateModule = create_basic_module(providers=[private_provider], name='PrivateModule')
    AliasModule = create_basic_module(
        providers=[alias_provider],
        imports=[PrivateModule],
        name='AliasModule',
    )
    AppModule = create_basic_module(imports=[AliasModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    alias_module = application.registry.get(AliasModule)
    assert_single_inaccessible_error(exc_info, required_type=_DecoratedService, from_module=alias_module)


async def test_decorator_dependencies_must_be_accessible(application_factory: ApplicationFactoryFunc) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService)
    decorator_provider.decorate(_decorate_service)
    PrivateModule = create_basic_module(
        providers=[scoped(_Secret)],
        name='PrivateModule',
    )
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_Secret,
        required_by=_decorate_service,
        from_module=decorator_module,
    )


async def test_decorator_dependency_can_be_exported(application_factory: ApplicationFactoryFunc) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService)
    decorator_provider.decorate(_decorate_service)
    SecretModule = create_basic_module(
        providers=[scoped(_Secret)],
        exports=[_Secret],
        name='SecretModule',
    )
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[SecretModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_decorator_dependencies_are_not_validated(application_factory: ApplicationFactoryFunc) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService, when=Has(_MissingActivation))
    decorator_provider.decorate(_decorate_service, when=Has(_MissingActivation))
    DecoratorModule = create_basic_module(providers=[decorator_provider], name='DecoratorModule')
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_decorator_on_active_target_is_not_validated(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService)
    decorator_provider.decorate(_decorate_service, when=Has(_MissingActivation))
    PrivateModule = create_basic_module(providers=[scoped(_Secret)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_local_factory_does_not_authorize_active_decorator_target(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService, when=Has(_MissingActivation))
    decorator_provider.decorate(_decorate_target_only)
    PrivateModule = create_basic_module(providers=[scoped(_DecoratedService)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_DecoratedService,
        required_by=_decorate_target_only,
        from_module=decorator_module,
    )


async def test_decorator_target_must_be_accessible(application_factory: ApplicationFactoryFunc) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.decorate(_decorate_target_only)
    PrivateModule = create_basic_module(providers=[scoped(_DecoratedService)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_DecoratedService,
        required_by=_decorate_target_only,
        from_module=decorator_module,
    )


async def test_global_decorator_output_does_not_make_its_private_target_accessible(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.decorate(_decorate_target_only)
    PrivateModule = create_basic_module(providers=[scoped(_DecoratedService)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        is_global=True,
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_DecoratedService,
        required_by=_decorate_target_only,
        from_module=decorator_module,
    )


async def test_global_decorator_can_use_a_target_provided_by_its_module(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService)
    decorator_provider.decorate(_decorate_target_only)
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        is_global=True,
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    await application_factory(AppModule).initialize()


async def test_inactive_global_decorator_does_not_authorize_private_target(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.decorate(_decorate_target_only, when=Has(_MissingActivation))
    alias_provider = Provider()
    alias_provider.alias(_DecoratedService, provides=_SecretAlias)
    PrivateModule = create_basic_module(providers=[scoped(_DecoratedService)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        is_global=True,
        name='DecoratorModule',
    )
    ConsumerModule = create_basic_module(
        providers=[alias_provider],
        imports=[PrivateModule],
        name='ConsumerModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule, ConsumerModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    consumer_module = application.registry.get(ConsumerModule)
    error = exc_info.value.exceptions[0]
    assert isinstance(error, DependencyInaccessibleError)
    assert error.required_type is _DecoratedService
    assert error.from_module is consumer_module


async def test_inactive_exported_decorator_does_not_authorize_private_target(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.decorate(_decorate_target_only, when=Has(_MissingActivation))
    alias_provider = Provider()
    alias_provider.alias(_DecoratedService, provides=_SecretAlias)
    PrivateModule = create_basic_module(providers=[scoped(_DecoratedService)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        exports=[_DecoratedService],
        name='DecoratorModule',
    )
    ConsumerModule = create_basic_module(
        providers=[alias_provider],
        imports=[DecoratorModule],
        name='ConsumerModule',
    )
    AppModule = create_basic_module(imports=[ConsumerModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    consumer_module = application.registry.get(ConsumerModule)
    error = exc_info.value.exceptions[0]
    assert isinstance(error, DependencyInaccessibleError)
    assert error.required_type is _DecoratedService
    assert error.from_module is consumer_module


async def test_dynamic_decorator_activation_validates_inaccessible_dependencies(
    application_factory: ApplicationFactoryFunc,
) -> None:
    dynamic_marker = Marker('dynamic-decorator')
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_DecoratedService)
    decorator_provider.decorate(_decorate_service, when=dynamic_marker)
    PrivateModule = create_basic_module(providers=[scoped(_Secret)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[
            singleton(_dynamic_activation),
            activator(_dynamic_activation_enabled, dynamic_marker),
            decorator_provider,
        ],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_Secret,
        required_by=_decorate_service,
        from_module=decorator_module,
    )


async def test_generic_decorator_dependencies_must_be_accessible(
    application_factory: ApplicationFactoryFunc,
) -> None:
    decorator_provider = Provider(scope=Scope.REQUEST)
    decorator_provider.provide(_make_int_box)
    decorator_provider.decorate(_decorate_box, when=Has(_Box[int]))
    PrivateModule = create_basic_module(providers=[scoped(_Secret)], name='PrivateModule')
    DecoratorModule = create_basic_module(
        providers=[decorator_provider],
        imports=[PrivateModule],
        name='DecoratorModule',
    )
    AppModule = create_basic_module(imports=[DecoratorModule], name='AppModule')

    application = application_factory(AppModule)
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    decorator_module = application.registry.get(DecoratorModule)
    assert_single_inaccessible_error(
        exc_info,
        required_type=_Secret,
        required_by=_decorate_box,
        from_module=decorator_module,
    )


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
        @override
        def create(self) -> User:
            return User()  # pragma: no cover

    class AdminUserFactory(IUserFactory[AdminUser]):
        @override
        def create(self) -> AdminUser:
            return AdminUser()  # pragma: no cover

    UsersModule = create_basic_module(
        providers=[
            scoped(AnyOf[IUserFactory[User], UserFactory], UserFactory),  # type: ignore[arg-type]
            scoped(AnyOf[IUserFactory[AdminUser], AdminUserFactory], AdminUserFactory),  # type: ignore[arg-type]
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
        @override
        def get(self) -> Entity:
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
