from __future__ import annotations

from typing import TYPE_CHECKING, Final

from waku.di import DependencyProvider, Object, Scoped
from waku.ext.mediator.handlers.map import RequestMap
from waku.ext.mediator.mediator import Mediator
from waku.extensions import OnApplicationInit, OnModuleInit

if TYPE_CHECKING:
    from waku.application import Application
    from waku.ext.mediator.handlers.dispatcher import RequestDispatcher
    from waku.ext.mediator.middlewares import MiddlewareChain
    from waku.module import Module


__all__ = [
    'MediatorAppExtension',
    'MediatorModuleExtension',
]


class MediatorAppExtension(OnApplicationInit):
    def __init__(
        self,
        middleware_chain: MiddlewareChain | None = None,
        dispatcher_class: type[RequestDispatcher] | None = None,
    ) -> None:
        self._middleware_chain = middleware_chain
        self._dispatcher_class = dispatcher_class

    def on_app_init(self, app: Application) -> None:
        dp = app.dependency_provider

        request_map = RequestMap()
        for module in app.modules:
            module_ext = next(
                (ext for ext in module.module_extensions if isinstance(ext, MediatorModuleExtension)), None
            )
            if not module_ext:
                continue

            request_map.merge(module_ext.request_map)

        for handler_type in request_map.registry.values():
            dp.register(Scoped(handler_type))

        mediator = self._build_mediator(dp, request_map)
        dp.register(Object(mediator, Mediator))

    def _build_mediator(self, dependency_provider: DependencyProvider, request_map: RequestMap) -> Mediator:
        return Mediator(
            request_map=request_map,
            dependency_provider=dependency_provider,
            middleware_chain=self._middleware_chain,
            dispatcher_class=self._dispatcher_class,
        )


class MediatorModuleExtension(OnModuleInit):
    def __init__(self, request_map: RequestMap, /) -> None:
        self.request_map: Final = request_map

    def on_module_init(self, module: Module) -> None:
        pass
