# mypy: disable-error-code="type-abstract"
from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.extensions import (
    AfterApplicationInit,
    OnApplicationInit,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleDestroy,
    OnModuleInit,
)
from waku.extensions.registry import ExtensionRegistry

from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from waku import Module, WakuApplication
    from waku.modules import ModuleMetadata


class OnApplicationInitExt(OnApplicationInit):
    @override
    async def on_app_init(self, app: WakuApplication) -> None:
        pass  # pragma: no cover


class AfterApplicationInitExt(AfterApplicationInit):
    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        pass  # pragma: no cover


class OnApplicationShutdownExt(OnApplicationShutdown):
    @override
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        pass  # pragma: no cover


class OnModuleConfigureExt(OnModuleConfigure):
    @override
    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        pass  # pragma: no cover


class OnModuleInitExt(OnModuleInit):
    @override
    async def on_module_init(self, module: Module) -> None:
        pass  # pragma: no cover


class OnModuleDestroyExt(OnModuleDestroy):
    @override
    async def on_module_destroy(self, module: Module) -> None:
        pass  # pragma: no cover


def test_registers_and_retrieves_application_extensions_by_type() -> None:
    # Arrange
    registry = ExtensionRegistry()

    on_init_ext = OnApplicationInitExt()
    after_init_ext = AfterApplicationInitExt()
    shutdown_ext = OnApplicationShutdownExt()

    registry.register_application_extension(on_init_ext)
    registry.register_application_extension(after_init_ext)
    registry.register_application_extension(shutdown_ext)

    # Act & Assert
    on_init_exts = registry.get_application_extensions(OnApplicationInit)
    assert len(on_init_exts) == 1
    assert on_init_exts[0] is on_init_ext

    after_init_exts = registry.get_application_extensions(AfterApplicationInit)
    assert len(after_init_exts) == 1
    assert after_init_exts[0] is after_init_ext

    shutdown_exts = registry.get_application_extensions(OnApplicationShutdown)
    assert len(shutdown_exts) == 1
    assert shutdown_exts[0] is shutdown_ext


def test_get_multi_protocol_app_extensions() -> None:
    # Arrange
    class MultiAppExt(OnApplicationInit, AfterApplicationInit):
        @override
        async def on_app_init(self, app: WakuApplication) -> None:
            pass  # pragma: no cover

        @override
        async def after_app_init(self, app: WakuApplication) -> None:
            pass  # pragma: no cover

    registry = ExtensionRegistry()
    multi_ext = MultiAppExt()
    registry.register_application_extension(multi_ext)

    # Act & Assert
    assert registry.get_application_extensions(OnApplicationInit) == [multi_ext]
    assert registry.get_application_extensions(AfterApplicationInit) == [multi_ext]


def test_get_application_extensions_returns_empty_list_when_no_match() -> None:
    # Arrange
    registry_empty = ExtensionRegistry()
    # Act & Assert
    result = registry_empty.get_application_extensions(OnApplicationShutdown)
    assert result == []


def test_registered_module_extensions_are_scoped_by_target_module() -> None:
    # Arrange
    registry = ExtensionRegistry()

    ModuleA = create_basic_module(name='ModuleA')
    ModuleB = create_basic_module(name='ModuleB')

    module_init_ext1 = OnModuleInitExt()
    module_init_ext2 = OnModuleInitExt()
    module_destroy_ext = OnModuleDestroyExt()

    registry.register_module_extension(ModuleA, module_init_ext1)
    registry.register_module_extension(ModuleB, module_init_ext2)
    registry.register_module_extension(ModuleA, module_destroy_ext)

    # Act & Assert
    module_a_init_exts = registry.get_module_extensions(ModuleA, OnModuleInit)
    assert len(module_a_init_exts) == 1
    assert module_a_init_exts[0] is module_init_ext1


def test_get_multi_protocol_module_extensions() -> None:
    # Arrange
    class MultiModuleExt(OnModuleInit, OnModuleDestroy):
        @override
        async def on_module_init(self, module: Module) -> None:
            pass  # pragma: no cover

        @override
        async def on_module_destroy(self, module: Module) -> None:
            pass  # pragma: no cover

    registry = ExtensionRegistry()
    multi_ext = MultiModuleExt()
    SomeModule = create_basic_module(name='SomeModule')
    registry.register_module_extension(SomeModule, multi_ext)

    # Act & Assert
    assert registry.get_module_extensions(SomeModule, OnModuleInit) == [multi_ext]
    assert registry.get_module_extensions(SomeModule, OnModuleDestroy) == [multi_ext]


def test_get_module_extensions_returns_empty_list_when_none_match_protocol() -> None:
    # Arrange
    registry = ExtensionRegistry()
    SomeModule = create_basic_module(name='SomeModule')
    registry.register_module_extension(SomeModule, OnModuleInitExt())

    # Act & Assert
    result = registry.get_module_extensions(SomeModule, OnModuleDestroy)
    assert result == []
