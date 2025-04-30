from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Final, TypeAlias
from uuid import UUID

from waku.modules import Module, ModuleCompiler, ModuleMetadata, ModuleRegistry, ModuleType

if TYPE_CHECKING:
    from dishka.provider import BaseProvider

    from waku import DynamicModule
    from waku.extensions import ModuleExtension


__all__ = [
    'AdjacencyMatrix',
    'ModuleRegistryBuilder',
]


AdjacencyMatrix: TypeAlias = dict[UUID, OrderedDict[UUID, str]]


class ModuleRegistryBuilder:
    def __init__(self, root_module_type: ModuleType, compiler: ModuleCompiler | None = None) -> None:
        self._compiler: Final = compiler or ModuleCompiler()
        self._root_module_type: Final = root_module_type
        self._modules: dict[UUID, Module] = {}
        self._providers: list[BaseProvider] = []
        self._extensions: list[ModuleExtension] = []

        self._metadata_cache: dict[ModuleType | DynamicModule, tuple[ModuleType, ModuleMetadata]] = {}

    def build(self) -> ModuleRegistry:
        modules, adjacency = self._collect_modules()
        root_module = self._register_modules(modules)
        return self._build_registry(root_module, adjacency)

    def _collect_modules(self) -> tuple[list[tuple[ModuleType, ModuleMetadata]], AdjacencyMatrix]:
        """Collect modules in post-order DFS."""
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

    def _register_modules(self, post_order: list[tuple[ModuleType, ModuleMetadata]]) -> Module:
        for type_, metadata in post_order:
            if metadata.id in self._modules:
                continue

            if type_ is self._root_module_type:
                metadata.is_global = True

            module = Module(type_, metadata)

            self._modules[module.id] = module
            self._providers.extend(module.providers)

        _, root_metadata = self._get_metadata(self._root_module_type)
        return self._modules[root_metadata.id]

    def _get_metadata(self, module_type: ModuleType | DynamicModule) -> tuple[ModuleType, ModuleMetadata]:
        """Get metadata with caching to avoid repeated extractions."""
        if module_type not in self._metadata_cache:
            self._metadata_cache[module_type] = self._compiler.extract_metadata(module_type)
        return self._metadata_cache[module_type]

    def _build_registry(self, root_module: Module, adjacency: AdjacencyMatrix) -> ModuleRegistry:
        # Store topological order (post_order DFS) for event triggering
        return ModuleRegistry(
            compiler=self._compiler,
            modules=self._modules,
            providers=self._providers,
            extensions=self._extensions,
            root_module=root_module,
            adjacency=adjacency,
        )
