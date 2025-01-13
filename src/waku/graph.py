from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from waku.modules import get_module_metadata

if TYPE_CHECKING:
    from collections.abc import Iterable

    from waku.modules import ModuleMetadata, ModuleType


class ModuleGraph:
    def __init__(self, root_module: ModuleType) -> None:
        self._root_module = root_module
        self._adjacency: dict[str, set[ModuleMetadata]] = defaultdict(set)

    def add_node(self, module: ModuleMetadata) -> None:
        self._adjacency[module.token].add(module)

    def add_edge(self, from_module: ModuleMetadata, to_module: ModuleMetadata) -> None:
        self._adjacency[from_module.token].add(to_module)

    def traverse(self, from_: ModuleType | None = None) -> Iterable[ModuleMetadata]:
        start_metadata = get_module_metadata(from_ or self._root_module)
        visited = {start_metadata.token}
        queue = deque([start_metadata])

        while queue:
            vertex = queue.popleft()
            yield vertex

            for neighbor in self._adjacency[vertex.token]:
                if neighbor.token not in visited:
                    visited.add(neighbor.token)
                    queue.append(neighbor)
