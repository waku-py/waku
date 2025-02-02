from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Self

from waku.di import DependencyProvider, InjectionContext, Object
from waku.graph import ModuleGraph

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable
    from types import TracebackType

    from waku.modules import ModuleMetadata, ModuleType


class ApplicationContainer:
    def __init__(self, dependency_provider: DependencyProvider, root_module: ModuleType) -> None:
        self._dependency_provider = dependency_provider
        self._root_module = root_module

        self._global_modules: set[ModuleMetadata] = set()
        self._modules: dict[str, ModuleMetadata] = {}

        self._graph = ModuleGraph(root_module)

        self._dependency_provider.register(Object(dependency_provider, DependencyProvider))
        self._dependency_provider.register(Object(self, ApplicationContainer))

        self._exit_stack = AsyncExitStack()

    def add_module(self, module: ModuleMetadata) -> bool:
        if self.has(module):
            return False

        self._modules[module.token] = module
        if self.is_global_module(module):
            self._global_modules.add(module)

        self._graph.add_node(module)
        for provider in module.providers:
            self._dependency_provider.register(provider)

        return True

    def has(self, module: ModuleMetadata) -> bool:
        return module.token in self._modules

    def get_modules(self, from_: ModuleType | None = None) -> Iterable[ModuleMetadata]:
        return self._graph.traverse(from_)

    @property
    def graph(self) -> ModuleGraph:
        return self._graph

    def is_global_module(self, module: ModuleMetadata) -> bool:
        return module.is_global or module.target is self._root_module

    @asynccontextmanager
    async def context(self) -> AsyncIterator[InjectionContext]:
        async with self._dependency_provider.context() as ctx:
            yield ctx

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(self._dependency_provider)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
