from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from waku.modules import Module, ModuleCompiler, ModuleType


class ModuleGraph:
    def __init__(self, root_module: Module, compiler: ModuleCompiler) -> None:
        self._root_module = root_module
        self._compiler = compiler

        self._modules: dict[UUID, Module] = {}
        self._adjacency: dict[UUID, set[UUID]] = defaultdict(set)

    def add_node(self, module: Module) -> None:
        self._modules.setdefault(module.id, module)
        self._adjacency[module.id].add(module.id)

    def add_edge(self, from_module: Module, to_module: Module) -> None:
        self._modules.setdefault(from_module.id, from_module)
        self._modules.setdefault(to_module.id, to_module)
        self._adjacency[from_module.id].add(to_module.id)

    def get(self, module_type: ModuleType) -> Module:
        module_id = self._compiler.extract_metadata(module_type)[1].id
        return self.get_by_id(module_id)

    def get_by_id(self, module_id: UUID) -> Module:
        module = self._modules.get(module_id)
        if module is None:
            msg = f'Module with ID {module_id} is not registered in the graph.'
            raise KeyError(msg)
        return module

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
