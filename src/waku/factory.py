from __future__ import annotations

from typing import TYPE_CHECKING

from waku.application import Application
from waku.container import ApplicationContainer
from waku.ext import DEFAULT_EXTENSIONS
from waku.extensions import ApplicationExtension, OnModuleConfigure
from waku.modules import ModuleMetadata, ModuleType, get_module_metadata

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku import LifespanFunc
    from waku.di import DependencyProvider


class ApplicationFactory:
    @classmethod
    async def create(
        cls,
        root_module: ModuleType,
        /,
        dependency_provider: DependencyProvider,
        lifespan: Sequence[LifespanFunc] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
    ) -> Application:
        container = await cls._build_container(root_module, dependency_provider)
        return Application(container, lifespan, extensions)

    @classmethod
    async def _build_container(
        cls,
        root_module: ModuleType,
        dependency_provider: DependencyProvider,
    ) -> ApplicationContainer:
        container = ApplicationContainer(dependency_provider, root_module)
        await cls._register_modules(container, root_module)
        return container

    @classmethod
    async def _register_modules(cls, container: ApplicationContainer, module: ModuleType) -> None:
        metadata = cls._get_metadata(module)

        if container.has(metadata):
            return

        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)

        container.add_module(metadata)

        for imported_module_type in metadata.imports:
            imported_module_metadata = cls._get_metadata(imported_module_type)
            container.graph.add_edge(metadata, imported_module_metadata)
            await cls._register_modules(container, imported_module_type)

    @staticmethod
    def _get_metadata(module: ModuleType) -> ModuleMetadata:
        try:
            return get_module_metadata(module)
        except AttributeError:
            msg = f'Module {type(module).__name__} is not decorated with @module'
            raise ValueError(msg) from None
