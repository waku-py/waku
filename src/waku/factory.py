from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dishka import STRICT_VALIDATION, make_async_container

from waku.application import WakuApplication
from waku.ext import DEFAULT_EXTENSIONS
from waku.extensions import OnModuleConfigure
from waku.graph import ModuleGraph
from waku.modules import Module, ModuleCompiler

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from dishka.provider import BaseProvider

    from waku.di import AsyncContainer
    from waku.extensions import ApplicationExtension
    from waku.lifespan import LifespanFunc
    from waku.modules import DynamicModule, ModuleType


__all__ = ['WakuFactory']


class WakuFactory:
    def __init__(
        self,
        root_module: ModuleType,
        /,
        context: dict[Any, Any] | None = None,
        lifespan: Sequence[LifespanFunc] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
    ) -> None:
        self._providers: list[BaseProvider] = []
        self._context = context

        self._modules: dict[UUID, Module] = {}
        self._compiler = ModuleCompiler()

        self._root_module_type = root_module
        self._root_module = self._add_module(root_module)[0]
        self._graph = ModuleGraph(self._root_module, self._compiler)

        self._lifespan = lifespan
        self._extensions = extensions

    def create(
        self,
    ) -> WakuApplication:
        self._register_modules(self._root_module_type)
        container = self._build_container()
        return WakuApplication(
            container=container,
            graph=self._graph,
            lifespan=self._lifespan,
            extensions=self._extensions,
        )

    def _add_module(self, module_type: ModuleType | DynamicModule) -> tuple[Module, bool]:
        type_, metadata = self._compiler.extract_metadata(module_type)
        if self._has(metadata.id):
            return self._modules[metadata.id], False

        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)

        module = Module(type_, metadata)

        for provider in module.providers:
            self._providers.append(provider)

        self._modules[module.id] = module

        return module, True

    def _has(self, id_: UUID) -> bool:
        return id_ in self._modules

    def _build_container(self) -> AsyncContainer:
        return make_async_container(
            *self._providers,
            context=self._context,
            validation_settings=STRICT_VALIDATION,
        )

    def _register_modules(
        self,
        module_type: ModuleType | DynamicModule,
    ) -> None:
        module, _ = self._add_module(module_type)

        for imported_module_type in module.imports:
            imported_module, imported_module_added = self._add_module(imported_module_type)
            if imported_module_added:
                self._graph.add_node(imported_module)
            self._graph.add_edge(module, imported_module)
            self._register_modules(imported_module_type)
