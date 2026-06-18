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
from waku.messaging.exceptions import HandlerNotFound
from waku.testing import create_test_app


@dataclass(frozen=True, kw_only=True)
class _Cmd(IRequest[None]):
    value: str


@dataclass(frozen=True, kw_only=True)
class _Evt(IEvent):
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
                extensions=[MessagingExtension().bind(CmdHandler)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            await dispatcher.invoke_request(container, _Cmd(value='hello'))

        assert called == ['hello']


class TestInvokeEvent:
    @staticmethod
    async def test_raises_handler_not_found_for_unregistered_event() -> None:
        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            with pytest.raises(HandlerNotFound, match='_Evt'):
                await dispatcher.invoke_event(container, _Evt(value='x'))

    @staticmethod
    async def test_invokes_all_registered_handlers() -> None:
        seen: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                seen.append(f'a:{event.value}')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                seen.append(f'b:{event.value}')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            await dispatcher.invoke_event(container, _Evt(value='hello'))

        assert set(seen) == {'a:hello', 'b:hello'}

    @staticmethod
    async def test_first_handler_failure_aborts_remaining() -> None:
        seen: list[str] = []

        class Failing(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                seen.append('failing')
                msg = 'boom'
                raise RuntimeError(msg)

        class NeverRuns(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
                seen.append('never')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(Failing, NeverRuns)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            with pytest.raises(RuntimeError, match='boom'):
                await dispatcher.invoke_event(container, _Evt(value='x'))

        assert seen == ['failing']
