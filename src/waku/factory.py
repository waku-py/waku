from __future__ import annotations

from asyncio import Lock
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from dishka import STRICT_VALIDATION, make_async_container

from waku.application import WakuApplication
from waku.extensions import DEFAULT_EXTENSIONS, ExtensionRegistry
from waku.modules import ModuleRegistryBuilder

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku import Module
    from waku.di import AsyncContainer, BaseProvider, IProviderFilter, Scope
    from waku.extensions import ApplicationExtension
    from waku.lifespan import LifespanFunc
    from waku.modules import ModuleType

__all__ = [
    'ContainerConfig',
    'WakuFactory',
]

_LockFactory: TypeAlias = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True, slots=True, kw_only=True)
class ContainerConfig:
    lock_factory: _LockFactory = Lock
    start_scope: Scope | None = None
    skip_validation: bool = False


class WakuFactory:
    def __init__(
        self,
        root_module_type: ModuleType,
        /,
        context: dict[Any, Any] | None = None,
        lifespan: Sequence[LifespanFunc] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
        container_config: ContainerConfig | None = None,
        provider_filter: IProviderFilter | None = None,
    ) -> None:
        self._root_module_type = root_module_type

        self._context = context
        self._lifespan = lifespan
        self._extensions = extensions
        self._container_config = container_config or ContainerConfig()
        self._provider_filter = provider_filter

    def create(self) -> WakuApplication:
        registry = ModuleRegistryBuilder(
            self._root_module_type,
            context=self._context,
            provider_filter=self._provider_filter,
            app_extensions=self._extensions,
        ).build()

        container = self._build_container(registry.providers)
        return WakuApplication(
            container=container,
            registry=registry,
            lifespan=self._lifespan,
            extension_registry=self._build_extension_registry(registry.modules),
        )

    def _build_extension_registry(self, modules: Iterable[Module]) -> ExtensionRegistry:
        extension_registry = ExtensionRegistry()
        for app_extension in self._extensions:
            extension_registry.register_application_extension(app_extension)
        for module in modules:
            for module_extension in module.extensions:
                extension_registry.register_module_extension(module.target, module_extension)
        return extension_registry

    def _build_container(self, providers: Sequence[BaseProvider]) -> AsyncContainer:
        return make_async_container(
            *providers,
            context=self._context,
            lock_factory=self._container_config.lock_factory,
            start_scope=self._container_config.start_scope,
            skip_validation=self._container_config.skip_validation,
            validation_settings=STRICT_VALIDATION,
        )
