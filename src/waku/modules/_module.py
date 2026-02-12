from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from waku.di import DEFAULT_COMPONENT, BaseProvider

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from dishka import Provider

    from waku.extensions import ModuleExtension
    from waku.modules._metadata import DynamicModule, ModuleMetadata, ModuleType


__all__ = ['Module']


class Module:
    __slots__ = (
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

        self.providers: Final[Sequence[Provider]] = metadata.providers
        self.imports: Final[Sequence[ModuleType | DynamicModule]] = metadata.imports
        self.exports: Final[Sequence[type[object] | ModuleType | DynamicModule]] = metadata.exports
        self.extensions: Final[Sequence[ModuleExtension]] = metadata.extensions
        self.is_global: Final[bool] = metadata.is_global

        self._provider: BaseProvider | None = None

    @property
    def name(self) -> str:
        return self.target.__name__

    @property
    def provider(self) -> BaseProvider:
        if self._provider is None:
            msg = f'Module {self.name} provider not yet created. Call create_provider() first.'
            raise RuntimeError(msg)
        return self._provider

    def create_provider(self) -> BaseProvider:
        cls = cast('type[_ModuleProvider]', type(f'{self.name}Provider', (_ModuleProvider,), {}))
        self._provider = cls(self.providers)
        return self._provider

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return self.id == other.id if isinstance(other, Module) else False


class _ModuleProvider(BaseProvider):
    def __init__(self, providers: Iterable[Provider]) -> None:
        super().__init__(DEFAULT_COMPONENT)
        for provider in providers:
            self.factories.extend(provider.factories)
            self.aliases.extend(provider.aliases)
            self.decorators.extend(provider.decorators)
            self.context_vars.extend(provider.context_vars)
            self.factory_union_mode.extend(provider.factory_union_mode)
            self.activators.extend(provider.activators)
