from __future__ import annotations

from asyncio import Lock
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

from dishka import STRICT_VALIDATION, make_async_container

from waku.application import WakuApplication
from waku.ext import DEFAULT_EXTENSIONS
from waku.modules import ModuleRegistryBuilder

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka.entities.validation_settigs import ValidationSettings

    from waku.di import AsyncContainer, BaseProvider, Scope
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
    validation_settings: ValidationSettings = field(default_factory=lambda: STRICT_VALIDATION)


class WakuFactory:
    def __init__(
        self,
        root_module_type: ModuleType,
        /,
        context: dict[Any, Any] | None = None,
        lifespan: Sequence[LifespanFunc] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
        container_config: ContainerConfig | None = None,
    ) -> None:
        self._root_module_type = root_module_type

        self._context = context
        self._lifespan = lifespan
        self._extensions = extensions
        self._container_config = container_config or ContainerConfig()

    def create(
        self,
    ) -> WakuApplication:
        registry = ModuleRegistryBuilder(self._root_module_type).build()
        container = self._build_container(registry.providers)
        return WakuApplication(
            container=container,
            registry=registry,
            lifespan=self._lifespan,
            extensions=self._extensions,
        )

    def _build_container(self, providers: Sequence[BaseProvider]) -> AsyncContainer:
        return make_async_container(
            *providers,
            context=self._context,
            lock_factory=self._container_config.lock_factory,
            start_scope=self._container_config.start_scope,
            skip_validation=self._container_config.skip_validation,
            validation_settings=self._container_config.validation_settings,
        )
