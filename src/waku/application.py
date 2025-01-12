"""Application module for the Waku microframework.

This module provides the core `Application` class, which serves as the entry point
for building modular monoliths and loosely coupled applications. It manages the
application's lifecycle, dependency injection, and extensions.
"""

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
    """Configuration for the Application class."""

    dependency_provider: DependencyProvider
    """The dependency provider for the application."""
    modules: list[Module] = field(default_factory=list)
    """List of modules to be imported by the application."""
    providers: list[Provider[Any]] = field(default_factory=list)
    """List of providers to be registered in the dependency container."""
    extensions: list[ApplicationExtension] = field(default_factory=list)
    """List of application extensions."""
    lifespan: list[ApplicationLifespan] = field(default_factory=lambda: [nullcontext()])
    """List of lifespan context managers or callables."""


class Application(Module):
    """Main application class that manages modules, providers, and extensions.

    Attributes:
        extensions: List of application extensions.
        dependency_provider: The dependency provider for the application.
    """

    def __init__(
        self,
        name: str,
        config: ApplicationConfig,
    ) -> None:
        config = self._before_init(config)

        super().__init__(
            name=name,
            providers=config.providers,
            imports=config.modules,
            is_global=True,
        )

        self.extensions: Final = config.extensions
        self.dependency_provider: Final = config.dependency_provider

        self._lifespan_managers: list[ApplicationLifespan] = config.lifespan
        self._exit_stack = AsyncExitStack()

        self._after_init()

    @property
    def modules(self) -> Sequence[Module]:
        """Get the list of modules imported by the application.

        Returns:
            Sequence of modules.
        """
        return self.imports

    def _before_init(self, config: ApplicationConfig) -> ApplicationConfig:
        """Prepare the application configuration before initialization.

        Args:
            config: The application configuration.

        Returns:
            The updated application configuration.
        """
        config.providers = [
            Object(self, Application),
            Object(config.dependency_provider, DependencyProvider),
            *config.providers,
        ]

        for ext in config.extensions:
            if isinstance(ext, OnApplicationInit):
                config = ext.on_app_init(config)

        return config

    def _after_init(self) -> None:
        """Perform actions after the application has been initialized."""
        self._register_providers()

        for ext in self.extensions:
            if isinstance(ext, AfterApplicationInit):
                ext.after_app_init(self)

    def _register_providers(self) -> None:
        """Register providers from all submodules."""
        for providers in chain(tuple(module.providers) for module in self.iter_submodules()):
            self.dependency_provider.register(*providers)

    @asynccontextmanager
    async def _lifespan(self) -> AsyncIterator[None]:
        """Manage the application's lifespan, including startup and shutdown handlers."""
        startup_handlers = [ext.on_app_startup for ext in self.extensions if isinstance(ext, OnApplicationStartup)]
        shutdown_handlers = [ext.on_app_shutdown for ext in self.extensions if isinstance(ext, OnApplicationShutdown)]

        for shutdown_handler in shutdown_handlers[::-1]:
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
