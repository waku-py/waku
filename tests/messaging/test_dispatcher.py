from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging import (
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.exceptions import HandlerNotFound
from waku.testing import create_test_app


@dataclass(frozen=True, kw_only=True)
class _Cmd(IRequest[None]):
    value: str


class TestInvokeRequest:
    @staticmethod
    async def test_raises_handler_not_found_for_unregistered_request() -> None:
        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            with pytest.raises(HandlerNotFound, match='_Cmd'):
                await dispatcher.invoke_request(container, _Cmd(value='x'))

    @staticmethod
    async def test_invokes_registered_handler() -> None:
        called: list[str] = []

        class CmdHandler(RequestHandler[_Cmd, None]):
            @override
            async def handle(self, request: _Cmd, /) -> None:
                called.append(request.value)

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(_Cmd, CmdHandler)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            await dispatcher.invoke_request(container, _Cmd(value='hello'))

        assert called == ['hello']
