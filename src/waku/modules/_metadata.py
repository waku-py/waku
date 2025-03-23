from __future__ import annotations

import functools
import uuid
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol, TypeAlias, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.di import Provider
    from waku.extensions import ModuleExtension

__all__ = [
    'DynamicModule',
    'ModuleCompiler',
    'ModuleMetadata',
    'ModuleType',
    'module',
]

_T = TypeVar('_T')

ModuleType: TypeAlias = 'type[object | HasModuleMetadata]'

MODULE_METADATA_KEY: Final = '__module_metadata__'


@dataclass(kw_only=True, slots=True)
class ModuleMetadata:
    providers: list[Provider[Any]] = field(default_factory=list)
    """List of providers for dependency injection."""
    imports: list[ModuleType | DynamicModule] = field(default_factory=list)
    """List of modules imported by this module."""
    exports: list[object | ModuleType | DynamicModule] = field(default_factory=list)
    """List of types or modules exported by this module."""
    extensions: list[ModuleExtension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""
    is_global: bool = False
    """Whether this module is global or not."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __hash__(self) -> int:
        return hash(self.id)


class HasModuleMetadata(Protocol):
    __module_metadata__: ModuleMetadata


@dataclass(kw_only=True, slots=True)
class DynamicModule(ModuleMetadata):
    parent_module: ModuleType

    def __hash__(self) -> int:
        return hash(self.id)


def module(
    *,
    providers: Sequence[Provider[Any]] = (),
    imports: Sequence[ModuleType | DynamicModule] = (),
    exports: Sequence[object | ModuleType | DynamicModule] = (),
    extensions: Sequence[ModuleExtension] = (),
    is_global: bool = False,
) -> Callable[[type[_T]], type[_T]]:
    """Decorator to define a module.

    Args:
        providers: Sequence of providers for dependency injection.
        imports: Sequence of modules imported by this module.
        exports: Sequence of types or modules exported by this module.
        extensions: Sequence of module extensions for lifecycle hooks.
        is_global: Whether this module is global or not.
    """

    def decorator(cls: type[_T]) -> type[_T]:
        metadata = ModuleMetadata(
            providers=list(providers),
            imports=list(imports),
            exports=list(exports),
            extensions=list(extensions),
            is_global=is_global,
        )
        setattr(cls, MODULE_METADATA_KEY, metadata)
        return cls

    return decorator


class ModuleCompiler:
    def extract_metadata(self, module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        try:
            return self._extract_metadata(cast(Hashable, module_type))
        except AttributeError:
            msg = f'{type(module_type).__name__} is not module'
            raise ValueError(msg) from None

    @staticmethod
    @functools.cache
    def _extract_metadata(module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        if isinstance(module_type, DynamicModule):
            parent_module = module_type.parent_module
            parent_metadata = cast(ModuleMetadata, getattr(parent_module, MODULE_METADATA_KEY))
            return parent_module, ModuleMetadata(
                providers=[*parent_metadata.providers, *module_type.providers],
                imports=[*parent_metadata.imports, *module_type.imports],
                exports=[*parent_metadata.exports, *module_type.exports],
                extensions=[*parent_metadata.extensions, *module_type.extensions],
                is_global=module_type.is_global,
                id=module_type.id,
            )
        return module_type, cast(ModuleMetadata, getattr(module_type, MODULE_METADATA_KEY))
