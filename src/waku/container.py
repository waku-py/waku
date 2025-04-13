from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Self, TypeVar

from waku.di import DependencyProvider, InjectionContext, Object, Provider
from waku.extensions import OnModuleConfigure
from waku.graph import ModuleGraph
from waku.modules import DynamicModule, Module, ModuleCompiler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
    from types import TracebackType
    from uuid import UUID

    from waku.modules import ModuleType


__all__ = ['WakuContainer']

_T = TypeVar('_T')


class WakuContainer:
    def __init__(self, dependency_provider: DependencyProvider, root_module: ModuleType) -> None:
        self._dependency_provider = dependency_provider

        self._modules: dict[UUID, Module] = {}
        self._compiler = ModuleCompiler()

        self._root_module = Module(*self._compiler.extract_metadata(root_module))
        self._graph = ModuleGraph(self._root_module)

        self._dependency_provider.register(Object(dependency_provider, DependencyProvider))
        self._dependency_provider.register(Object(self, WakuContainer))

        self._exit_stack = AsyncExitStack()

    def add_module(self, module_type: ModuleType | DynamicModule) -> tuple[Module, bool]:
        type_, metadata = self._compiler.extract_metadata(module_type)
        if self.has(metadata.id):
            return self._modules[metadata.id], False

        for extension in metadata.extensions:
            if isinstance(extension, OnModuleConfigure):
                extension.on_module_configure(metadata)

        module = Module(type_, metadata)

        self._modules[module.id] = module
        self._graph.add_node(module)

        for provider in module.providers:
            self._dependency_provider.register(provider)

        return module, True

    def has(self, id_: UUID) -> bool:
        return id_ in self._modules

    def get_module(self, module_type: ModuleType | DynamicModule) -> Module:
        return self._modules[self._compiler.extract_metadata(module_type)[1].id]

    def get_module_by_id(self, id_: UUID) -> Module:
        return self._modules[id_]

    def get_modules(self, from_: Module | None = None) -> Iterable[Module]:
        return self._graph.traverse(from_)

    @contextmanager
    def override(self, *providers: Provider[Any]) -> Iterator[None]:
        with self._dependency_provider.override(*providers):
            yield

    @property
    def graph(self) -> ModuleGraph:
        return self._graph

    @property
    def compiler(self) -> ModuleCompiler:
        return self._compiler

    def is_global_module(self, module: Module) -> bool:
        return module.is_global or module is self._root_module

    @asynccontextmanager
    async def context(self, context: Mapping[Any, Any] | None = None) -> AsyncIterator[InjectionContext]:
        async with self._dependency_provider.context(context) as ctx:
            yield ctx

    async def get(self, type_: type[_T]) -> _T:
        return await self._dependency_provider.get(type_)

    async def get_all(self, type_: type[_T]) -> Iterable[_T]:
        return await self._dependency_provider.get_all(type_)

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
