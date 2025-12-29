from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from waku.di import BaseProvider
    from waku.modules._metadata import DynamicModule, ModuleCompiler, ModuleType
    from waku.modules._module import Module
    from waku.modules._registry_builder import AdjacencyMatrix


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
        adjacency: AdjacencyMatrix,
    ) -> None:
        self._compiler = compiler
        self._root_module = root_module
        self._modules = modules
        self._providers = tuple(providers)
        self._adjacency = adjacency
        self._parent_to_module = self._build_parent_mapping(modules)

    @staticmethod
    def _build_parent_mapping(modules: dict[UUID, Module]) -> dict[type, Module]:
        """Build mapping from parent module classes to their registered DynamicModule instances."""
        mapping: dict[type, Module] = {}
        for mod in modules.values():
            if isinstance(mod.target, type):
                mapping[mod.target] = mod
        return mapping

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
        # For plain module classes, check if they're registered via parent mapping first.
        # This handles the case where ConfigModule.register() was imported,
        # but ConfigModule (the class) is being exported.
        if isinstance(module_type, type) and module_type in self._parent_to_module:
            return self._parent_to_module[module_type]

        module_id = self.compiler.extract_metadata(module_type)[1].id
        return self.get_by_id(module_id)

    def get_by_id(self, module_id: UUID) -> Module:
        module = self._modules.get(module_id)
        if module is None:
            msg = f'Module with ID {module_id} is not registered in the graph.'
            raise KeyError(msg)
        return module

    def traverse(self, from_: Module | None = None) -> Iterator[Module]:
        """Traverse the module graph in depth-first post-order (children before parent) recursively.

        Args:
            from_: Start module (default: root)

        Yields:
            Module: Each traversed module (post-order)
        """
        start_module = from_ or self._root_module
        visited: set[UUID] = set()

        def _dfs(module: Module) -> Iterator[Module]:
            if module.id in visited:
                return

            visited.add(module.id)

            # Process children first (maintain original order)
            neighbor_ids = self._adjacency[module.id]
            for neighbor_id in neighbor_ids:
                if neighbor_id == module.id:
                    continue
                neighbor = self.get_by_id(neighbor_id)
                yield from _dfs(neighbor)

            # Process current module after children (post-order)
            yield module

        yield from _dfs(start_module)
