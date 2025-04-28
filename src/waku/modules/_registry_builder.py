from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Final

from waku.extensions import ModuleExtension, OnModuleConfigure
from waku.modules import Module, ModuleCompiler, ModuleMetadata, ModuleRegistry, ModuleType

if TYPE_CHECKING:
    from uuid import UUID

    from dishka.provider import BaseProvider

    from waku import DynamicModule


__all__ = ['ModuleRegistryBuilder']


class ModuleRegistryBuilder:
    def __init__(self, root_module_type: ModuleType, compiler: ModuleCompiler | None = None) -> None:
        self._compiler: Final = compiler or ModuleCompiler()
        self._root_module_type: ModuleType = root_module_type
        self._modules: dict[UUID, Module] = {}
        self._providers: list[BaseProvider] = []
        self._extensions: list[ModuleExtension] = []
        self._adjacency: dict[UUID, OrderedDict[UUID, str]] = defaultdict(OrderedDict)

    def build(self) -> ModuleRegistry:
        modules = self._collect_modules()
        root_module = self._register_modules(modules)
        return self._build_registry(root_module)

    def _collect_modules(self) -> list[tuple[ModuleType, ModuleMetadata]]:
        """Collect modules in post-order DFS."""
        stack: list[tuple[ModuleType | DynamicModule, bool]] = [(self._root_module_type, False)]
        post_order: list[tuple[ModuleType, ModuleMetadata]] = []
        visited: set[UUID] = set()
        while stack:
            current_type, processed = stack.pop()
            type_, metadata = self._compiler.extract_metadata(current_type)

            module_id = metadata.id
            if module_id in visited:
                continue

            if processed:
                post_order.append((type_, metadata))
                visited.add(module_id)
                continue

            stack.append((current_type, True))
            for imported in reversed(metadata.imports):
                _, imported_metadata = self._compiler.extract_metadata(imported)
                if imported_metadata.id not in visited:
                    stack.append((imported, False))

        return post_order

    def _register_modules(self, modules: list[tuple[ModuleType, ModuleMetadata]]) -> Module:
        for type_, metadata in modules:
            if metadata.id in self._modules:
                continue
            self._register_module(type_, metadata)
        _, root_metadata = self._compiler.extract_metadata(self._root_module_type)
        return self._modules[root_metadata.id]

    def _register_module(self, type_: ModuleType, metadata: ModuleMetadata) -> Module:
        module = Module(type_, metadata)
        self._modules[module.id] = module
        self._providers.extend(module.providers)
        self._update_adjacency(module)
        return module

    def _handle_extensions(self, metadata: ModuleMetadata) -> None:
        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)
            self._extensions.append(extension)

    def _update_adjacency(self, module: Module) -> None:
        if module.id not in self._adjacency:
            self._adjacency[module.id][module.id] = str(module)

        for imported_module_type in module.imports:
            _, imported_metadata = self._compiler.extract_metadata(imported_module_type)
            if imported_metadata.id not in self._adjacency[module.id]:
                self._adjacency[module.id][imported_metadata.id] = str(imported_metadata)

    def _build_registry(self, root_module: Module) -> ModuleRegistry:
        return ModuleRegistry(
            compiler=self._compiler,
            modules=self._modules,
            providers=self._providers,
            extensions=self._extensions,
            root_module=root_module,
            adjacency=self._adjacency,
        )
