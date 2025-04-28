from __future__ import annotations

from collections import defaultdict, deque
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
        self._adjacency: dict[UUID, set[UUID]] = defaultdict(set)

    def build(self) -> ModuleRegistry:
        root_module = self._register_module(self._root_module_type)
        self._register_modules(root_module)
        return self._build_registry(root_module)

    def _register_modules(self, root_module: Module) -> None:
        stack: deque[ModuleType | DynamicModule] = deque(root_module.imports)
        visited: set[UUID] = {root_module.id}
        while stack:
            module_type = stack.popleft()
            registered_module = self._register_module(module_type)
            if registered_module.id in visited:
                continue
            visited.add(registered_module.id)
            stack.extend(registered_module.imports)

    def _register_module(self, module_type: ModuleType | DynamicModule) -> Module:
        type_, metadata = self._compiler.extract_metadata(module_type)
        if existing_module := self._modules.get(metadata.id):
            return existing_module

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
        self._adjacency[module.id].add(module.id)
        for imported_module_type in module.imports:
            _, imported_metadata = self._compiler.extract_metadata(imported_module_type)
            self._adjacency[module.id].add(imported_metadata.id)

    def _build_registry(self, root_module: Module) -> ModuleRegistry:
        return ModuleRegistry(
            compiler=self._compiler,
            modules=self._modules,
            providers=self._providers,
            extensions=self._extensions,
            root_module=root_module,
            adjacency=self._adjacency,
        )
