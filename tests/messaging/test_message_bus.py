from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging import (
    IEvent,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.exceptions import RequestHandlerNotFound
from waku.testing import create_test_app


@dataclass(frozen=True, kw_only=True)
class _Result:
    value: str


@dataclass(frozen=True, kw_only=True)
class _Command(IRequest[_Result]):
    name: str


class _CommandHandler(RequestHandler[_Command, _Result]):
    @override
    async def handle(self, request: _Command, /) -> _Result:
        return _Result(value=request.name)


class _UnregisteredCommand(IRequest[None]):
    pass


@dataclass(frozen=True)
class _SomeEvent(IEvent):
    pass


async def test_invoke_returns_handler_result() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind_request(_Command, _CommandHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        result = await bus.invoke(_Command(name='hello'))
        assert result == _Result(value='hello')


async def test_send_delegates_to_handler() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind_request(_Command, _CommandHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.send(_Command(name='test'))


async def test_invoke_raises_for_unregistered_request() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with pytest.raises(RequestHandlerNotFound, match='_UnregisteredCommand request is not registered'):
            await bus.invoke(_UnregisteredCommand())


async def test_publish_without_handlers_does_nothing() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())
