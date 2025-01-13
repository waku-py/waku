from __future__ import annotations

from typing import TYPE_CHECKING

from waku.application import Application
from waku.container import ApplicationContainer
from waku.ext import DEFAULT_EXTENSIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.di import DependencyProvider
    from waku.extensions import ApplicationExtension
    from waku.lifespan import LifespanFunc
    from waku.modules import DynamicModule, ModuleType


class ApplicationFactory:
    @classmethod
    def create(
        cls,
        root_module: ModuleType,
        /,
        dependency_provider: DependencyProvider,
        lifespan: Sequence[LifespanFunc] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
    ) -> Application:
        container = cls._build_container(root_module, dependency_provider)
        return Application(container, lifespan, extensions)

    @classmethod
    def _build_container(
        cls,
        root_module: ModuleType,
        dependency_provider: DependencyProvider,
    ) -> ApplicationContainer:
        container = ApplicationContainer(dependency_provider, root_module)
        cls._register_modules(container, root_module)
        return container

    @classmethod
    def _register_modules(cls, container: ApplicationContainer, module_type: ModuleType | DynamicModule) -> None:
        module, _ = container.add_module(module_type)

        for imported_module_type in module.imports:
            imported_module, _ = container.add_module(imported_module_type)
            container.graph.add_edge(module, imported_module)
            cls._register_modules(container, imported_module_type)
