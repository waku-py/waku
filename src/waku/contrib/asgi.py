from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping

    from waku.application import Application

    Scope: TypeAlias = MutableMapping[str, Any]
    Message: TypeAlias = MutableMapping[str, Any]
    Receive: TypeAlias = Callable[[], Awaitable[Message]]
    Send: TypeAlias = Callable[[Message], Awaitable[None]]

    ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


__all__ = ['ApplicationMiddleware']


class ApplicationMiddleware:
    def __init__(self, app: ASGIApp, *, application: Application) -> None:
        self.app = app
        self._application = application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with self._application.dependency_provider.context():
            await self.app(scope, receive, send)
