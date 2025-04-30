"""Tests for dynamic module functionality."""

from tests.data import A, AddDepOnConfigure
from tests.module_utils import create_basic_module, create_dynamic_module
from waku import WakuFactory
from waku.di import scoped


def test_dynamic_module_configuration() -> None:
    """Dynamic modules should properly handle configuration extensions."""
    SomeDynamicModule = create_dynamic_module(
        extensions=[AddDepOnConfigure(scoped(A))],
        name='SomeDynamicModule',
    )
    AppModule = create_basic_module(
        imports=[SomeDynamicModule],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    assert len(application.registry.get(SomeDynamicModule).providers) == 1


def test_module_configuration_with_multiple_import_paths() -> None:
    """Module configuration should be applied only once when imported through multiple paths."""
    SomeModule = create_dynamic_module(
        extensions=[AddDepOnConfigure(scoped(A))],
        name='SomeModule',
    )
    ModuleA = create_basic_module(
        imports=[SomeModule],
        name='ModuleA',
    )
    ModuleB = create_basic_module(
        imports=[SomeModule],
        name='ModuleB',
    )
    AppModule = create_basic_module(
        imports=[ModuleA, ModuleB],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    assert len(application.registry.get(SomeModule).providers) == 1
