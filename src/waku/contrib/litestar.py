from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Final

from litestar.middleware import MiddlewareProtocol
from litestar.plugins import InitPluginProtocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar import Litestar
    from litestar.config.app import AppConfig
    from litestar.types import ASGIApp, Receive, Scope, Send

    from waku.application import WakuApplication

__all__ = [
    'WakuMiddleware',
    'WakuPlugin',
]

_STATE_KEY: Final = '__waku_application__'
_SCOPE_CONTEXT_KEY: Final = '__waku_injection_context__'


class WakuMiddleware(MiddlewareProtocol):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        app: Litestar = scope['app']
        application: WakuApplication = app.state[_STATE_KEY]

        async with application.container.context() as ctx:
            scope[_SCOPE_CONTEXT_KEY] = ctx  # type: ignore[typeddict-unknown-key]
            await self.app(scope, receive, send)


async def _after_exception(exception: BaseException, scope: Scope) -> None:
    if _SCOPE_CONTEXT_KEY in scope:
        await scope[_SCOPE_CONTEXT_KEY].__aexit__(  # type: ignore[typeddict-item]
            type(exception),
            exception,
            exception.__traceback__,
        )


class WakuPlugin(InitPluginProtocol):
    def __init__(self, application: WakuApplication) -> None:
        self._application = application

    @contextlib.asynccontextmanager
    async def _lifespan(self, _: Litestar) -> AsyncIterator[None]:
        async with self._application:
            yield

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        app_config.state[_STATE_KEY] = self._application
        app_config.middleware.append(WakuMiddleware)
        app_config.lifespan.append(self._lifespan)  # pyright: ignore [reportUnknownMemberType]
        app_config.after_exception.append(_after_exception)  # pyright: ignore [reportUnknownMemberType]
        return app_config
