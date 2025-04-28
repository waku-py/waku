from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Final

from waku.extensions import ModuleExtension, OnModuleConfigure
from waku.modules import Module, ModuleCompiler, ModuleRegistry, ModuleType

if TYPE_CHECKING:
    from collections.abc import Iterator
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
        self._adjacency: dict[UUID, set[UUID]] = defaultdict(set)

    def build(self) -> ModuleRegistry:
        root_module = self._register_module(self._root_module_type)
        self._register_modules(root_module)
        self._build_adjacency()
        return self._finalize_registry(root_module)

    def _register_modules(self, root_module: Module) -> None:
        stack: deque[ModuleType | DynamicModule] = deque(root_module.imports)
        visited: set[UUID] = {root_module.id}
        while stack:
            module_type = stack.popleft()
            _, metadata = self._compiler.extract_metadata(module_type)
            if metadata.id in visited:
                continue
            visited.add(metadata.id)
            self._register_module(module_type)
            for imported in metadata.imports:
                stack.append(imported)

    def _register_module(self, module_type: ModuleType | DynamicModule) -> Module:
        type_, metadata = self._compiler.extract_metadata(module_type)
        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)
            self._extensions.append(extension)
        module = Module(type_, metadata)
        self._modules[module.id] = module
        self._providers.extend(module.providers)
        return module

    def _build_adjacency(self) -> None:
        for module in self._modules.values():
            self._add_node(module)
        for from_module, to_module in self._iter_import_edges():
            self._add_edge(from_module, to_module)

    def _finalize_registry(self, root_module: Module) -> ModuleRegistry:
        return ModuleRegistry(
            compiler=self._compiler,
            modules=self._modules,
            providers=self._providers,
            extensions=self._extensions,
            root_module=root_module,
            adjacency=self._adjacency,
        )

    def _add_node(self, module: Module) -> None:
        self._modules.setdefault(module.id, module)
        self._adjacency[module.id].add(module.id)

    def _add_edge(self, from_module: Module, to_module: Module) -> None:
        self._modules.setdefault(from_module.id, from_module)
        self._modules.setdefault(to_module.id, to_module)
        self._adjacency[from_module.id].add(to_module.id)

    def _iter_import_edges(self) -> Iterator[tuple[Module, Module]]:
        for module in self._modules.values():
            for imported in module.imports:
                _, imported_metadata = self._compiler.extract_metadata(imported)
                imported_id = imported_metadata.id
                if imported_id in self._modules:
                    yield module, self._modules[imported_id]
