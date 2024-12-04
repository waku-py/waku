from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AsyncExitStack, nullcontext
from functools import cached_property
from itertools import chain
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self, cast

from aioinject import Container, InjectionContext, Object, Provider
from aioinject.context import context_var as aioinject_context
from diator.mediator import Mediator
from diator.middlewares import MiddlewareChain

from app.seedwork.application.diator import CommandHandlerMap, MediatorModule

if TYPE_CHECKING:
    from aioinject.extensions import ContextExtension
    from diator.middlewares import Middleware
    from diator.requests import Request


class ApplicationModule:
    def __init__(
        self,
        name: str,
        *,
        providers: Iterable[Provider[Any]] = (),
        command_handlers: CommandHandlerMap[Any, Any] | None = None,
        imports: Iterable[ApplicationModule] = (),
        exports: Iterable[ApplicationModule | type[object]] = (),
    ) -> None:
        self.name = name
        self._providers = providers
        self._command_handlers: CommandHandlerMap[Any, Any] = command_handlers or {}
        self._imports = imports
        self._exports = exports

    @cached_property
    def providers(self) -> tuple[Provider[Any], ...]:
        return tuple(self._providers)

    @cached_property
    def command_handlers(self) -> CommandHandlerMap[Any, Any]:
        return self._command_handlers

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'ApplicationModule[{self.name}]'


class Application(ApplicationModule):
    def __init__(
        self,
        name: str,
        *,
        providers: Iterable[Provider[Any]] = (),
        command_handlers: CommandHandlerMap[Any, Any] | None = None,
        imports: Iterable[ApplicationModule] = (),
        exports: Iterable[ApplicationModule | type[object]] = (),
        command_middlewares: Iterable[Middleware] | None = None,
    ) -> None:
        super().__init__(
            name,
            providers=providers,
            command_handlers=command_handlers,
            imports=imports,
            exports=exports,
        )
        self._command_middlewares = command_middlewares
        self._exit_stack = AsyncExitStack()

    @cached_property
    def container(self) -> Container:
        return _create_container(application=self, command_middlewares=self._command_middlewares)

    @property
    def modules(self) -> Sequence[ApplicationModule]:
        return self._modules

    def context(self, extensions: Sequence[ContextExtension] = ()) -> InjectionContext:
        if current_context := aioinject_context.get(None):
            return cast(InjectionContext, nullcontext(current_context))
        return self.container.context(extensions)

    async def execute(self, request: Request) -> Any:
        async with self.context() as ctx:
            mediator = await ctx.resolve(Mediator)
            return await mediator.send(request)

    async def __aenter__(self) -> Self:
        await self._exit_stack.enter_async_context(self.container)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.aclose()

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Application[{self.name}]'


def _create_container(
    application: Application,
    command_middlewares: Iterable[Middleware] | None,
) -> Container:
    container = Container()
    # Self provider
    container.register(Object(container, Container))
    # Application provider
    container.register(Object(application, Application))

    # Register providers from core
    command_handlers = []
    for module in application.modules:
        # Register providers from core
        for provider in module.providers:
            container.register(provider)

        command_handlers.append(module.command_handlers.items())

    if command_middlewares:
        middleware_chain = MiddlewareChain()
        for middleware in command_middlewares:
            middleware_chain.add(middleware)
    else:
        middleware_chain = MediatorModule.build_default_middleware_chain()

    MediatorModule(
        command_handlers=dict(chain.from_iterable(command_handlers)),
        middleware_chain=middleware_chain,
    ).init(application, container)

    return container
