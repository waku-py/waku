from __future__ import annotations

from collections import OrderedDict, defaultdict
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, TypeAlias
from uuid import UUID

from waku.extensions import OnModuleRegistration
from waku.modules._metadata import ModuleCompiler, ModuleMetadata, ModuleType
from waku.modules._metadata_registry import ModuleMetadataRegistry
from waku.modules._module import Module
from waku.modules._registry import ModuleRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku import DynamicModule
    from waku.di import BaseProvider
    from waku.extensions import ApplicationExtension


__all__ = [
    'AdjacencyMatrix',
    'ModuleRegistryBuilder',
]


AdjacencyMatrix: TypeAlias = dict[UUID, OrderedDict[UUID, str]]


class ModuleRegistryBuilder:
    def __init__(
        self,
        root_module_type: ModuleType,
        compiler: ModuleCompiler | None = None,
        context: dict[Any, Any] | None = None,
        app_extensions: Sequence[ApplicationExtension] = (),
    ) -> None:
        self._compiler: Final = compiler or ModuleCompiler()
        self._root_module_type: Final = root_module_type
        self._context: Final = context
        self._app_extensions: Final = app_extensions
        self._modules: dict[UUID, Module] = {}
        self._providers: list[BaseProvider] = []

        self._metadata_cache: dict[ModuleType | DynamicModule, tuple[ModuleType, ModuleMetadata]] = {}

    def build(self) -> ModuleRegistry:
        modules, adjacency = self._collect_modules()
        self._execute_registration_hooks(modules)
        root_module = self._register_modules(modules)
        return self._build_registry(root_module, adjacency)

    def _collect_modules(self) -> tuple[list[tuple[ModuleType, ModuleMetadata]], AdjacencyMatrix]:
        visited: set[UUID] = set()
        post_order: list[tuple[ModuleType, ModuleMetadata]] = []
        adjacency: AdjacencyMatrix = defaultdict(OrderedDict)
        self._collect_modules_recursive(self._root_module_type, visited, post_order, adjacency)
        return post_order, adjacency

    def _collect_modules_recursive(
        self,
        current_type: ModuleType | DynamicModule,
        visited: set[UUID],
        post_order: list[tuple[ModuleType, ModuleMetadata]],
        adjacency: AdjacencyMatrix,
    ) -> None:
        type_, metadata = self._get_metadata(current_type)
        if metadata.id in visited:
            return

        adjacency[metadata.id][metadata.id] = type_.__name__

        for imported in metadata.imports:
            imported_type, imported_metadata = self._get_metadata(imported)
            adjacency[metadata.id][imported_metadata.id] = imported_type.__name__
            if imported_metadata.id not in visited:
                self._collect_modules_recursive(imported, visited, post_order, adjacency)

        post_order.append((type_, metadata))
        visited.add(metadata.id)

    def _execute_registration_hooks(
        self,
        modules: list[tuple[ModuleType, ModuleMetadata]],
    ) -> None:
        metadata_by_type = dict(modules)
        topological_order = tuple(mod_type for mod_type, _ in modules)
        registry = ModuleMetadataRegistry(
            metadata_by_type=metadata_by_type,
            topological_order=topological_order,
        )

        read_only_context: Mapping[Any, Any] | None = MappingProxyType(self._context) if self._context else None

        for ext in self._app_extensions:
            if isinstance(ext, OnModuleRegistration):
                ext.on_module_registration(registry, self._root_module_type, read_only_context)

        for module_type, metadata in modules:
            for ext in metadata.extensions:
                if isinstance(ext, OnModuleRegistration):
                    ext.on_module_registration(registry, module_type, read_only_context)

    def _register_modules(self, post_order: list[tuple[ModuleType, ModuleMetadata]]) -> Module:
        for type_, metadata in post_order:
            if metadata.id in self._modules:
                continue

            if type_ is self._root_module_type:
                metadata.is_global = True

            module = Module(type_, metadata)

            self._modules[module.id] = module
            self._providers.append(module.create_provider())

        _, root_metadata = self._get_metadata(self._root_module_type)
        return self._modules[root_metadata.id]

    def _get_metadata(self, module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        if module_type not in self._metadata_cache:
            self._metadata_cache[module_type] = self._compiler.extract_metadata(module_type)
        return self._metadata_cache[module_type]

    def _build_registry(self, root_module: Module, adjacency: AdjacencyMatrix) -> ModuleRegistry:
        return ModuleRegistry(
            compiler=self._compiler,
            modules=self._modules,
            providers=self._providers,
            root_module=root_module,
            adjacency=adjacency,
        )
