from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Final, cast

from waku.di import DEFAULT_COMPONENT, BaseProvider

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from waku.extensions import ModuleExtension
    from waku.modules._metadata import DynamicModule, ModuleMetadata, ModuleType


__all__ = ['Module']


class Module:
    __slots__ = (
        '__dict__',
        '_provider',
        'exports',
        'extensions',
        'id',
        'imports',
        'is_global',
        'providers',
        'target',
    )

    def __init__(self, module_type: ModuleType, metadata: ModuleMetadata) -> None:
        self.id: Final[UUID] = metadata.id
        self.target: Final[ModuleType] = module_type

        self.providers: Final[Sequence[BaseProvider]] = metadata.providers
        self.imports: Final[Sequence[ModuleType | DynamicModule]] = metadata.imports
        self.exports: Final[Sequence[type[object] | ModuleType | DynamicModule]] = metadata.exports
        self.extensions: Final[Sequence[ModuleExtension]] = metadata.extensions
        self.is_global: Final[bool] = metadata.is_global

    @property
    def name(self) -> str:
        return self.target.__name__

    @cached_property
    def provider(self) -> BaseProvider:
        cls = cast(type[_ModuleProvider], type(f'{self.name}Provider', (_ModuleProvider,), {}))
        return cls(self.providers)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return self.id == other.id if isinstance(other, Module) else False


class _ModuleProvider(BaseProvider):
    def __init__(self, providers: Iterable[BaseProvider]) -> None:
        super().__init__(DEFAULT_COMPONENT)
        for provider in providers:
            self.factories.extend(provider.factories)
            self.aliases.extend(provider.aliases)
            self.decorators.extend(provider.decorators)
            self.context_vars.extend(provider.context_vars)
