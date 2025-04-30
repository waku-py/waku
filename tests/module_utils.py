"""Common module patterns used across tests."""

from collections.abc import Sequence
from typing import Any

from tests.data import DependentService, Service
from waku import module
from waku.di import scoped
from waku.extensions import ModuleExtension
from waku.modules import DynamicModule


def create_basic_module(
    *,
    providers: Sequence[Any] | None = None,
    exports: Sequence[Any] | None = None,
    imports: Sequence[Any] | None = None,
    name: str | None = None,
    extensions: Sequence[ModuleExtension] | None = None,
    is_global: bool = False,
) -> type:
    """Create a basic module with given configuration."""

    @module(
        providers=list(providers or []),
        exports=list(exports or []),
        imports=list(imports or []),
        extensions=list(extensions or []),
        is_global=is_global,
    )
    class BasicModule:
        pass

    if name:
        BasicModule.__name__ = name

    return BasicModule


def create_service_module(
    *,
    exports: bool = False,
    name: str | None = None,
) -> type:
    """Create a module with Service provider."""

    @module(
        providers=[scoped(Service)],
        exports=[Service] if exports else [],
    )
    class ServiceModule:
        pass

    if name:
        ServiceModule.__name__ = name

    return ServiceModule


def create_dependent_service_module(
    *,
    imports: Sequence[Any] | None = None,
    name: str | None = None,
) -> type:
    """Create a module with DependentService provider."""

    @module(
        providers=[scoped(DependentService)],
        imports=list(imports or []),
    )
    class DependentServiceModule:
        pass

    if name:
        DependentServiceModule.__name__ = name

    return DependentServiceModule


def create_dynamic_module(
    *,
    providers: Sequence[Any] | None = None,
    exports: Sequence[Any] | None = None,
    imports: Sequence[Any] | None = None,
    extensions: Sequence[Any] | None = None,
    name: str | None = None,
) -> DynamicModule:
    """Create a dynamic module with given configuration."""

    @module()
    class DynamicModuleParent:
        @classmethod
        def register(cls) -> DynamicModule:
            return DynamicModule(
                parent_module=cls,
                providers=list(providers or []),
                exports=list(exports or []),
                imports=list(imports or []),
                extensions=list(extensions or []),
            )

    if name:
        DynamicModuleParent.__name__ = name

    return DynamicModuleParent.register()
