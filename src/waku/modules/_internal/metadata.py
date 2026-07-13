from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from waku._internal.sentinel import MISSING
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import OnModuleConfigure

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Sequence

    from dishka import Provider

    from waku.extensions import ModuleExtension

__all__ = [
    'DynamicModule',
    'HasModuleMetadata',
    'ModuleCompiler',
    'ModuleMetadata',
    'ModuleType',
    'module',
]

_ClassT = TypeVar('_ClassT')


_MODULE_METADATA_KEY: Final = '__module_metadata__'


@dataclass(kw_only=True, slots=True)
class ModuleMetadata:
    providers: list[Provider] = field(default_factory=list)
    """List of providers for dependency injection."""
    imports: list[ModuleType | DynamicModule] = field(default_factory=list)
    """List of modules imported by this module."""
    exports: list[type[object] | ModuleType | DynamicModule] = field(default_factory=list)
    """List of types or modules exported by this module."""
    extensions: list[ModuleExtension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""
    is_global: bool = False
    """Whether this module is global or not."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __hash__(self) -> int:
        return hash(self.id)


@runtime_checkable
class HasModuleMetadata(Protocol):
    __module_metadata__: ModuleMetadata


ModuleType: TypeAlias = type[object | HasModuleMetadata]


@dataclass(kw_only=True, slots=True)
class DynamicModule(ModuleMetadata):
    parent_module: ModuleType

    def __hash__(self) -> int:
        return hash(self.id)


def _require_module_metadata(module: object, *, source: ModuleType | DynamicModule) -> ModuleMetadata:
    """Read a module's attached metadata, raising ``ImproperlyConfiguredError`` only when it is genuinely absent.

    Probing for the attribute (instead of wrapping the whole extraction) keeps a real ``AttributeError``
    from an ``on_module_configure`` hook propagating with its own traceback, rather than being swallowed
    and relabeled "is not a module".

    Raises:
        ImproperlyConfiguredError: If *module* has no attached module metadata (i.e. *source* is not a module).
    """
    metadata = getattr(module, _MODULE_METADATA_KEY, MISSING)
    if metadata is MISSING:
        msg = f'{type(source).__name__} is not a module'
        raise ImproperlyConfiguredError(msg)
    return cast('ModuleMetadata', metadata)


def module(
    *,
    providers: Sequence[Provider] = (),
    imports: Sequence[ModuleType | DynamicModule] = (),
    exports: Sequence[type[object] | ModuleType | DynamicModule] = (),
    extensions: Sequence[ModuleExtension] = (),
    is_global: bool = False,
) -> Callable[[type[_ClassT]], type[_ClassT]]:
    """Decorator to define a module.

    Args:
        providers: Sequence of providers for dependency injection.
        imports: Sequence of modules imported by this module.
        exports: Sequence of types or modules exported by this module.
        extensions: Sequence of module extensions for lifecycle hooks.
        is_global: Whether this module is global or not.
    """

    def decorator(cls: type[_ClassT]) -> type[_ClassT]:
        metadata = ModuleMetadata(
            providers=list(providers),
            imports=list(imports),
            exports=list(exports),
            extensions=list(extensions),
            is_global=is_global,
        )
        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)

        setattr(cls, _MODULE_METADATA_KEY, metadata)
        return cls

    return decorator


class ModuleCompiler:
    """Resolves module metadata with an instance-scoped memo.

    The memo keeps `on_module_configure` idempotent across repeated `extract_metadata` calls
    within one compiler (one app registry) and dies with the compiler — a process-global cache
    would pin every `DynamicModule` (fresh `uuid4` identity per `.register()`) forever.
    """

    def __init__(self) -> None:
        self._cache: dict[Hashable, tuple[ModuleType, ModuleMetadata]] = {}

    def extract_metadata(self, module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        key = cast('Hashable', module_type)
        if key not in self._cache:
            self._cache[key] = self._extract_metadata(module_type)
        return self._cache[key]

    @staticmethod
    def _extract_metadata(module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        if isinstance(module_type, DynamicModule):
            parent_module = module_type.parent_module
            parent_metadata = _require_module_metadata(parent_module, source=module_type)
            metadata = ModuleMetadata(
                providers=[*parent_metadata.providers, *module_type.providers],
                imports=[*parent_metadata.imports, *module_type.imports],
                exports=[*parent_metadata.exports, *module_type.exports],
                extensions=[*parent_metadata.extensions, *module_type.extensions],
                is_global=module_type.is_global,
                id=module_type.id,
            )
            for extension in metadata.extensions:
                if isinstance(extension, OnModuleConfigure):
                    extension.on_module_configure(metadata)
            return parent_module, metadata

        return module_type, _require_module_metadata(module_type, source=module_type)
