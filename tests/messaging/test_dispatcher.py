from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging import (
    EventHandler,
    IEvent,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.exceptions import HandlerNotFound, MultipleHandlersRegistered
from waku.messaging.registry import MessageRegistry
from waku.testing import create_test_app


@dataclass(frozen=True)
class _Evt(IEvent):
    value: str


@dataclass(frozen=True, kw_only=True)
class _Cmd(IRequest[None]):
    value: str


class TestInvokeRequest:
    @staticmethod
    async def test_raises_handler_not_found_for_unregistered_request() -> None:
        registry = MessageRegistry()
        registry.freeze()

        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as container,
        ):
            dispatcher = MessageDispatcher(container, registry)
            with pytest.raises(HandlerNotFound, match='_Cmd'):
                await dispatcher.invoke_request(_Cmd(value='x'))

    @staticmethod
    async def test_raises_multiple_handlers_registered() -> None:
        class HandlerA(RequestHandler[_Cmd, None]):
            @override
            async def handle(self, request: _Cmd, /) -> None: ...  # pragma: no cover

        class HandlerB(RequestHandler[_Cmd, None]):
            @override
            async def handle(self, request: _Cmd, /) -> None: ...  # pragma: no cover

        registry = MessageRegistry()
        registry.handler_map.bind(_Cmd, HandlerA)
        registry.handler_map.bind(_Cmd, HandlerB)
        registry.freeze()

        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as container,
        ):
            dispatcher = MessageDispatcher(container, registry)
            with pytest.raises(MultipleHandlersRegistered, match='_Cmd'):
                await dispatcher.invoke_request(_Cmd(value='x'))


class TestExecuteForHandler:
    @staticmethod
    async def test_executes_only_specified_event_handler() -> None:
        called: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
                called.append(f'A:{event.value}')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(f'B:{event.value}')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(_Evt, HandlerA, HandlerB)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await container.get(MessageDispatcher)
            await dispatcher.execute_for_handler(_Evt(value='z'), HandlerB)

        assert called == ['B:z']

    @staticmethod
    async def test_executes_request_handler() -> None:
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
            dispatcher = await container.get(MessageDispatcher)
            await dispatcher.execute_for_handler(_Cmd(value='x'), CmdHandler)

        assert called == ['x']
