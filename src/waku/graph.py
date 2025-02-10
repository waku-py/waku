from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from waku.modules import Module


class ModuleGraph:
    def __init__(self, root_module: Module) -> None:
        self._root_module = root_module
        self._adjacency: dict[UUID, set[Module]] = defaultdict(set)

    def add_node(self, module: Module) -> None:
        self._adjacency[module.id].add(module)

    def add_edge(self, from_module: Module, to_module: Module) -> None:
        self._adjacency[from_module.id].add(to_module)

    def traverse(self, from_: Module | None = None) -> Iterable[Module]:
        start_module = from_ or self._root_module
        visited = {start_module.id}
        queue = deque([start_module])

        while queue:
            vertex = queue.popleft()
            yield vertex

            for neighbor in self._adjacency[vertex.id]:
                if neighbor.id not in visited:
                    visited.add(neighbor.id)
                    queue.append(neighbor)
