from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections import OrderedDict
    from collections.abc import Iterator
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
        adjacency: dict[UUID, OrderedDict[UUID, str]],
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

    def traverse(self, from_: Module | None = None) -> Iterator[Module]:
        """Traverse the module graph in depth-first post-order (children before parent) using a stack.

        Args:
            from_: Start module (default: root)

        Yields:
            Module: Each traversed module (post-order)
        """
        start_module = from_ or self._root_module
        visited: set[UUID] = set()
        stack: list[tuple[Module, bool]] = [(start_module, False)]

        while stack:
            module, processed = stack.pop()

            if processed:
                yield module
                continue

            if module.id in visited:
                continue

            visited.add(module.id)

            # Push the current module to process after its children
            stack.append((module, True))

            # Push children to be processed first (in reverse order to maintain original order in DFS)
            neighbor_ids = self._adjacency[module.id]
            for neighbor_id in reversed(neighbor_ids.keys()):
                if neighbor_id == module.id:
                    continue
                neighbor = self.get_by_id(neighbor_id)
                stack.append((neighbor, False))

    def is_global_module(self, module: Module) -> bool:
        return module.is_global or module == self._root_module
