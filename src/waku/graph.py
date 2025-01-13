from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Self

import networkx as nx

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.module import Module


class ModuleGraph:
    def __init__(self, root: Module) -> None:
        self._root = root
        self._graph = nx.DiGraph()

    @classmethod
    def build(cls, root: Module) -> Self:
        self = cls(root)
        self._build_module_graph_recursive(root)
        return self

    def _build_module_graph_recursive(self, module: Module) -> None:
        self._graph.add_node(module.name, module=module)

        for imported_module in module.imports:
            # First ensure the imported module node exists
            if imported_module.name not in self._graph:
                self._build_module_graph_recursive(imported_module)

            # Then add the edge after both nodes exist
            self._graph.add_edge(module.name, imported_module.name)

    def iterate_modules(self, from_: Module | None = None) -> Iterator[Module]:
        """Iterate through modules in the graph by their distance from root.

        Args:
            from_: The module to start iterating from. If None, starts from the root module.

        Yields:
            Iterator yielding lists of Module objects for each distance level.
        """
        yield from chain.from_iterable(self._iterate_layers(from_))

    def _iterate_layers(self, from_: Module | None = None) -> Iterator[list[Module]]:
        for layer in nx.bfs_layers(self._graph, from_.name if from_ else self._root.name):
            yield [module for name in layer if (module := self._graph.nodes[name].get('module'))]
