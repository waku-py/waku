from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, Self, TypeAlias

from waku.di import DependencyProvider, Object
from waku.extensions import (
    AfterApplicationInit,
    ApplicationExtension,
    OnApplicationInit,
    OnApplicationShutdown,
    OnApplicationStartup,
)
from waku.module import Module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from waku.di import Provider

__all__ = [
    'Application',
    'ApplicationConfig',
    'ApplicationLifespan',
]

ApplicationLifespan: TypeAlias = (
    Callable[['Application'], AbstractAsyncContextManager[None]] | AbstractAsyncContextManager[None]
)


@dataclass(kw_only=True, slots=True)
class ApplicationConfig:
    modules: list[Module] = field(default_factory=list)
    providers: list[Provider[Any]] = field(default_factory=list)
    extensions: list[ApplicationExtension] = field(default_factory=list)
    lifespan: list[ApplicationLifespan] = field(default_factory=list)


class Application(Module):
    def __init__(
        self,
        name: str,
        *,
        dependency_provider: DependencyProvider,
        modules: Sequence[Module] | None = None,
        providers: Sequence[Provider[Any]] | None = None,
        extensions: Sequence[ApplicationExtension] | None = None,
        lifespan: Sequence[ApplicationLifespan] | None = None,
    ) -> None:
        config = ApplicationConfig(
            modules=list(modules or []),
            providers=list(providers or []),
            extensions=list(extensions or []),
            lifespan=list(lifespan) if lifespan else [nullcontext()],
        )
        config = self._before_init(config, dependency_provider)

        super().__init__(
            name=name,
            providers=config.providers,
            imports=config.modules,
            is_global=True,
        )

        self.extensions: Final = config.extensions
        self.dependency_provider: Final = dependency_provider

        self._lifespan_managers: list[ApplicationLifespan] = config.lifespan
        self._exit_stack = AsyncExitStack()

        self._after_init()

    @property
    def modules(self) -> Sequence[Module]:
        return self.imports

    def _before_init(self, config: ApplicationConfig, dependency_provider: DependencyProvider) -> ApplicationConfig:
        config.providers = [
            Object(self, Application),
            Object(dependency_provider, DependencyProvider),
            *config.providers,
        ]

        for ext in config.extensions:
            if isinstance(ext, OnApplicationInit):
                config = ext.on_app_init(config)

        return config

    def _after_init(self) -> None:
        self._register_providers()

        for ext in self.extensions:
            if isinstance(ext, AfterApplicationInit):
                ext.after_app_init(self)

    def _register_providers(self) -> None:
        for providers in chain(tuple(module.providers) for module in self.iter_submodules()):
            self.dependency_provider.register(*providers)

    @asynccontextmanager
    async def _lifespan(self) -> AsyncIterator[None]:
        startup_handlers = [ext.on_app_startup for ext in self.extensions if isinstance(ext, OnApplicationStartup)]
        shutdown_handlers = [ext.on_app_shutdown for ext in self.extensions if isinstance(ext, OnApplicationShutdown)]

        for shutdown_handler in reversed(shutdown_handlers):
            self._exit_stack.push_async_callback(shutdown_handler, self)

        for manager in self._lifespan_managers:
            ctx_manager = manager(self) if not isinstance(manager, AbstractAsyncContextManager) else manager
            await self._exit_stack.enter_async_context(ctx_manager)

        for startup_handler in startup_handlers:
            await startup_handler(self)

        yield

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(self._lifespan())
        await self._exit_stack.enter_async_context(self.dependency_provider)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    def __repr__(self) -> str:
        return f'Application[{self.name}]'
