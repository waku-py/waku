from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging import (
    CallNext,
    EventHandler,
    IEvent,
    IMessageBus,
    IPipelineBehavior,
    IRequest,
    MessageT,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    ResponseT,
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


async def test_publish_runs_global_behaviors_per_handler() -> None:
    called: list[str] = []

    class TrackingBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append(f'behavior:{type(message).__name__}')
            return await call_next()

    class HandlerA(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler_a')

    class HandlerB(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler_b')

    async with (
        create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig(pipeline_behaviors=[TrackingBehavior])),
            ],
            extensions=[MessagingExtension().bind_event(_SomeEvent, [HandlerA, HandlerB])],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called == ['behavior:_SomeEvent', 'handler_a', 'behavior:_SomeEvent', 'handler_b']


async def test_publish_runs_scoped_behavior_for_bound_event() -> None:
    called: list[str] = []

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('scoped_behavior')
            return await call_next()

    class Handler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind_event(_SomeEvent, [Handler], behaviors=[ScopedBehavior])],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called == ['scoped_behavior', 'handler']


async def test_publish_runs_global_then_scoped_behaviors() -> None:
    called: list[str] = []

    class GlobalBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('global')
            return await call_next()

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('scoped')
            return await call_next()

    class Handler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(pipeline_behaviors=[GlobalBehavior]))],
            extensions=[MessagingExtension().bind_event(_SomeEvent, [Handler], behaviors=[ScopedBehavior])],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called == ['global', 'scoped', 'handler']


async def test_publish_scoped_behavior_does_not_run_for_other_event() -> None:
    called: list[str] = []

    @dataclass(frozen=True)
    class OtherEvent(IEvent):
        pass

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:  # pragma: no cover
            called.append('scoped_behavior')
            return await call_next()

    class SomeEventHandler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:  # pragma: no cover
            called.append('some_handler')

    class OtherEventHandler(EventHandler[OtherEvent]):
        @override
        async def handle(self, event: OtherEvent, /) -> None:
            called.append('other_handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[
                MessagingExtension()
                .bind_event(_SomeEvent, [SomeEventHandler], behaviors=[ScopedBehavior])
                .bind_event(OtherEvent, [OtherEventHandler]),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(OtherEvent())

    assert called == ['other_handler']


async def test_publish_without_handlers_does_nothing() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())
