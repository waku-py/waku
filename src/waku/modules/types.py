from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias
from uuid import uuid4

if TYPE_CHECKING:
    from waku.di import Provider
    from waku.extensions import ModuleExtension

ModuleType: TypeAlias = 'type[object | HasModuleMetadata] | DynamicModule'


@dataclass(kw_only=True, slots=True)
class _CommonModuleMetadata:
    providers: list[Provider[Any]] = field(default_factory=list)
    """List of providers for dependency injection."""
    imports: list[ModuleType] = field(default_factory=list)
    """List of modules imported by this module."""
    exports: list[object | ModuleType] = field(default_factory=list)
    """List of types or modules exported by this module."""
    extensions: list[ModuleExtension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""


@dataclass(kw_only=True, slots=True)
class ModuleMetadata(_CommonModuleMetadata):
    is_global: bool = False
    """Whether this module is global or not."""
    target: type[HasModuleMetadata]
    """Wrapped class."""

    token: str = field(init=False)
    distance: int = field(default=0)

    @property
    def name(self) -> str:
        return self.target.__name__

    def __post_init__(self) -> None:
        self.token = f'{self.name}-{uuid4()}'
        self.distance = sys.maxsize if self.is_global else 0

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'

    def __hash__(self) -> int:
        return hash(self.token)

    def __lt__(self, other: ModuleMetadata) -> bool:
        return self.distance < other.distance


class HasModuleMetadata(Protocol):
    __module_metadata__: ModuleMetadata


@dataclass(kw_only=True, slots=True)
class DynamicModule(_CommonModuleMetadata):
    parent_module: type[object | HasModuleMetadata]
