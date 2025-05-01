"""Common module patterns used across tests."""

from collections.abc import Sequence
from typing import Any

from waku import module
from waku.extensions import ModuleExtension
from waku.modules import DynamicModule, ModuleType


def create_basic_module(
    *,
    providers: Sequence[Any] | None = None,
    exports: Sequence[Any] | None = None,
    imports: Sequence[Any] | None = None,
    name: str | None = None,
    extensions: Sequence[ModuleExtension] | None = None,
    is_global: bool = False,
) -> ModuleType:
    """Create a basic module with given configuration."""
    cls: ModuleType = module(
        providers=list(providers or []),
        exports=list(exports or []),
        imports=list(imports or []),
        extensions=list(extensions or []),
        is_global=is_global,
    )(type(name or 'BasicModule', (object,), {}))

    return cls


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

    if name:  # pragma: no cover
        DynamicModuleParent.__name__ = name

    return DynamicModuleParent.register()
