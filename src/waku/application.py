"""Application module for the Waku microframework.

This module provides the core `Application` class, which serves as the entry point
for building modular monoliths and loosely coupled applications. It manages the
application's lifecycle, dependency injection, and extensions.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Final, Self, TypeAlias

from waku._lifespan import LifespanWrapper
from waku.di import DependencyProvider, Object
from waku.extensions import (
    AfterApplicationInit,
    ApplicationExtension,
    ApplicationLifespan,
    OnApplicationConfigure,
    OnApplicationInit,
    OnModuleInit,
)
from waku.graph import ModuleGraph

if TYPE_CHECKING:
    from types import TracebackType

    from waku.module import Module

__all__ = [
    'Application',
    'ApplicationConfig',
    'ApplicationExtension',
    'ApplicationLifespanFunc',
]

ApplicationLifespanFunc: TypeAlias = (
    Callable[['Application'], AbstractAsyncContextManager[None]] | AbstractAsyncContextManager[None]
)


@dataclass(kw_only=True, slots=True)
class ApplicationConfig:
    """Configuration for the Application class."""

    """The dependency provider for the application."""
    lifespan: list[ApplicationLifespanFunc] = field(default_factory=lambda: [nullcontext()])
    """List of lifespan context managers or callables."""
    extensions: list[ApplicationExtension] = field(default_factory=list)
    """List of application extensions for lifecycle hooks."""


class Application:
    """Main application class that manages modules, providers, and extensions.

    Attributes:
        extensions: List of application extensions.
        dependency_provider: The dependency provider for the application.
    """

    def __init__(
        self,
        *,
        root: Module,
        dependency_provider: DependencyProvider,
        config: ApplicationConfig | None = None,
    ) -> None:
        if not root.is_global:
            msg = 'Root module must be a global module.'
            raise ValueError(msg)

        config: ApplicationConfig = config or ApplicationConfig()
        for ext in config.extensions:
            if isinstance(ext, OnApplicationConfigure):
                config = ext.on_app_configure(config)

        config.extensions = list(
            chain(
                [LifespanWrapper(lifespan) for lifespan in config.lifespan],
                config.extensions,
            )
        )

        self.root = root
        self.dependency_provider: Final = dependency_provider
        self.extensions = config.extensions

        self._graph = ModuleGraph.build(self.root)
        self._exit_stack = AsyncExitStack()
        self._is_initialized: bool = False

    async def init(self) -> Self:
        if self._is_initialized:
            return self

        self._register_app()
        self._register_modules()

        await self._on_init_hooks()
        await self._after_app_init_hooks()

        self._is_initialized = True
        return self

    @property
    def module_graph(self) -> ModuleGraph:
        return self._graph

    def _register_modules(self) -> None:
        for providers in tuple(module.providers for module in self._graph.iterate_modules()):
            self.dependency_provider.register(*providers)

    async def _on_init_hooks(self) -> None:
        globals_first = sorted(
            self._graph.iterate_modules(),
            key=lambda m: sys.maxsize if m.is_global else 0,
            reverse=True,
        )
        async with asyncio.TaskGroup() as tg:
            for ext in self.extensions:
                if isinstance(ext, OnApplicationInit):
                    tg.create_task(ext.on_app_init(self))

            for module in globals_first:
                for ext in module.extensions:
                    if isinstance(ext, OnModuleInit):
                        tg.create_task(ext.on_module_init(self))

    async def _after_app_init_hooks(self) -> None:
        """Perform actions after the application has been initialized."""
        async with asyncio.TaskGroup() as tg:
            for ext in self.extensions:
                if isinstance(ext, AfterApplicationInit):
                    tg.create_task(ext.after_app_init(self))

    def _register_app(self) -> None:
        """Register providers from all submodules."""
        providers = (
            Object(self, Application),
            Object(self.dependency_provider, DependencyProvider),
        )
        self.dependency_provider.register(*providers)

    async def _lifespan(self) -> None:
        """Manage the application's lifespan, including startup and shutdown handlers."""
        for extension in self.extensions:
            if isinstance(extension, ApplicationLifespan):
                await self._exit_stack.enter_async_context(extension.lifespan(self))

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        if not self._is_initialized:
            await self.init()
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
