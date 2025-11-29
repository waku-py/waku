from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, TypeAlias

if TYPE_CHECKING:
    from dishka import Provider

    from waku import DynamicModule
    from waku.di._providers import ProviderSpec
    from waku.modules import ModuleType

__all__ = [
    'ActivationBuilder',
    'ActivationContext',
    'Activator',
    'ConditionalProvider',
    'Has',
    'IProviderFilter',
    'ProviderFilter',
]


class ActivationBuilder(Protocol):
    @abstractmethod
    def has_active(self, type_: Any) -> bool:
        raise NotImplementedError


class ActivationContext(NamedTuple):
    """Context passed to activators for provider activation decisions."""

    container_context: dict[Any, Any] | None
    module_type: ModuleType | DynamicModule
    provided_type: Any
    builder: ActivationBuilder


Activator: TypeAlias = Callable[[ActivationContext], bool]


@dataclass(frozen=True, slots=True)
class Has:
    """Activator that checks if a provider for a type is registered."""

    type_: Any

    def __call__(self, ctx: ActivationContext) -> bool:
        return ctx.builder.has_active(self.type_)


@dataclass(frozen=True, slots=True)
class ConditionalProvider:
    """Provider with activation condition."""

    provider: Provider
    when: Activator
    provided_type: Any


class IProviderFilter(Protocol):
    """Strategy for filtering providers based on activation context."""

    def filter(
        self,
        providers: list[ProviderSpec],
        context: dict[Any, Any] | None,
        module_type: ModuleType | DynamicModule,
        builder: ActivationBuilder,
    ) -> list[Provider]: ...


OnSkipCallback: TypeAlias = Callable[[ConditionalProvider, ActivationContext], None]


@dataclass(slots=True)
class ProviderFilter:
    """Default provider filter implementation."""

    on_skip: OnSkipCallback | None = field(default=None)

    def filter(
        self,
        providers: list[ProviderSpec],
        context: dict[Any, Any] | None,
        module_type: ModuleType | DynamicModule,
        builder: ActivationBuilder,
    ) -> list[Provider]:
        result: list[Provider] = []

        for spec in providers:
            if isinstance(spec, ConditionalProvider):
                ctx = ActivationContext(
                    container_context=context,
                    module_type=module_type,
                    provided_type=spec.provided_type,
                    builder=builder,
                )
                if spec.when(ctx):
                    result.append(spec.provider)
                elif self.on_skip:
                    self.on_skip(spec, ctx)
            else:
                result.append(spec)

        return result
