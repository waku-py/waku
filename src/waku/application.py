"""Application module for the Waku microframework.

This module provides the core `Application` class, which serves as the entry point
for building modular monoliths and loosely coupled applications. It manages the
application's lifecycle, dependency injection, and extensions.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, Self, TypeAlias

from waku._lifespan import LifespanWrapperExtension
from waku._utils import iter_submodules
from waku.di import DependencyProvider, Object, Provider
from waku.extensions import (
    AfterApplicationInit,
    ApplicationExtension,
    ApplicationLifespan,
    Extension,
    OnApplicationInit,
)
from waku.module import Module

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

__all__ = [
    'Application',
    'ApplicationConfig',
    'ApplicationLifespanFunc',
]

ApplicationLifespanFunc: TypeAlias = (
    Callable[['Application'], AbstractAsyncContextManager[None]] | AbstractAsyncContextManager[None]
)


@dataclass(kw_only=True, slots=True)
class ApplicationConfig:
    """Configuration for the Application class."""

    dependency_provider: DependencyProvider
    """The dependency provider for the application."""
    lifespan: list[ApplicationLifespanFunc] = field(default_factory=lambda: [nullcontext()])
    """List of lifespan context managers or callables."""

    providers: list[Provider[Any]] = field(default_factory=list)
    """List of providers for dependency injection."""
    modules: list[Module] = field(default_factory=list)
    """List of modules imported by this module."""
    extensions: list[Extension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""


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
        self._exit_stack = AsyncExitStack()

        config.extensions.extend(LifespanWrapperExtension(lifespan) for lifespan in config.lifespan)
        config.extensions.extend(self._collect_application_extensions(config))
        config = self._before_init(config)

        super().__init__(
            name=name,
            providers=config.providers,
            imports=config.modules,
            extensions=config.extensions,
            is_global=True,
        )

        self.dependency_provider: Final = config.dependency_provider
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

    async def _lifespan(self) -> None:
        """Manage the application's lifespan, including startup and shutdown handlers."""
        for extension in self.extensions:
            if isinstance(extension, ApplicationLifespan):
                await self._exit_stack.enter_async_context(extension.lifespan(self))

    def _collect_application_extensions(self, config: ApplicationConfig) -> list[Extension]:  # noqa: PLR6301
        return [
            ext
            for ext in chain.from_iterable(module.extensions for module in iter_submodules(*config.modules))
            if isinstance(ext, ApplicationExtension)
        ]

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        await self._lifespan()
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
