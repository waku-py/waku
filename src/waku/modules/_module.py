from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from waku.di import DEFAULT_COMPONENT, ActivationBuilder, BaseProvider, IProviderFilter

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import Any
    from uuid import UUID

    from dishka import Provider

    from waku.di import ProviderSpec
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

        self.providers: Final[Sequence[ProviderSpec]] = metadata.providers
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
        """Get the aggregated provider for this module.

        This property returns the provider created by create_provider().
        Must be called after create_provider() has been invoked.

        Raises:
            RuntimeError: If create_provider() has not been called yet.
        """
        if self._provider is None:
            msg = f'Module {self.name} provider not yet created. Call create_provider() first.'
            raise RuntimeError(msg)
        return self._provider

    def create_provider(
        self,
        context: dict[Any, Any] | None,
        builder: ActivationBuilder,
        provider_filter: IProviderFilter,
    ) -> BaseProvider:
        """Create aggregated provider with activation filtering applied.

        Args:
            context: Context dict for activation decisions.
            builder: Activation builder for checking if types are registered.
            provider_filter: Filter strategy for conditional provider activation.

        Returns:
            BaseProvider with only active providers aggregated.
        """
        active_providers = provider_filter.filter(
            list(self.providers),
            context=context,
            module_type=self.target,
            builder=builder,
        )

        cls = cast('type[_ModuleProvider]', type(f'{self.name}Provider', (_ModuleProvider,), {}))
        self._provider = cls(active_providers)
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
    """Aggregates factories from filtered providers."""

    def __init__(self, providers: Iterable[Provider]) -> None:
        super().__init__(DEFAULT_COMPONENT)
        for provider in providers:
            self.factories.extend(provider.factories)
            self.aliases.extend(provider.aliases)
            self.decorators.extend(provider.decorators)
            self.context_vars.extend(provider.context_vars)
