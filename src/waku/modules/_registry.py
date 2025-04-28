from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from waku.di import BaseProvider
    from waku.extensions import ModuleExtension
    from waku.modules._metadata import DynamicModule, ModuleCompiler, ModuleType
    from waku.modules._module import Module

__all__ = ['ModuleRegistry']


class ModuleRegistry:
    """Immutable registry and graph for module queries, traversal, and lookups."""

    def __init__(
        self,
        *,
        compiler: ModuleCompiler,
        root_module: Module,
        modules: dict[UUID, Module],
        providers: list[BaseProvider],
        extensions: list[ModuleExtension],
        adjacency: dict[UUID, set[UUID]],
    ) -> None:
        self._compiler = compiler
        self._root_module = root_module
        self._modules = modules
        self._providers = tuple(providers)
        self._extensions = tuple(extensions)
        self._adjacency = adjacency

    @property
    def root_module(self) -> Module:
        return self._root_module

    @property
    def modules(self) -> tuple[Module, ...]:
        return tuple(self._modules.values())

    @property
    def providers(self) -> tuple[BaseProvider, ...]:
        return self._providers

    @property
    def compiler(self) -> ModuleCompiler:
        return self._compiler

    def get(self, module_type: ModuleType | DynamicModule) -> Module:
        module_id = self._compiler.extract_metadata(module_type)[1].id
        return self.get_by_id(module_id)

    def get_by_id(self, module_id: UUID) -> Module:
        module = self._modules.get(module_id)
        if module is None:
            msg = f'Module with ID {module_id} is not registered in the graph.'
            raise KeyError(msg)
        return module

    def get_by_type(self, module_type: ModuleType | DynamicModule) -> Module:
        _, metadata = self._compiler.extract_metadata(module_type)
        return self._modules[metadata.id]

    def traverse(self, from_: Module | None = None) -> Iterable[Module]:
        start_module = from_ or self._root_module
        visited = {start_module.id}
        queue = deque([start_module])

        while queue:
            vertex = queue.popleft()
            yield vertex

            neighbors = self._adjacency[vertex.id]
            unvisited = (n for n in neighbors if n not in visited)

            for neighbor in unvisited:
                visited.add(neighbor)
                queue.append(self.get_by_id(neighbor))

    def is_global_module(self, module: Module) -> bool:
        return module.is_global or module == self._root_module
