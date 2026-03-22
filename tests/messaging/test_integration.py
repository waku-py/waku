from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

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
from waku.messaging.context import MessageContext, get_message_context, try_get_message_context
from waku.testing import create_test_app


@dataclass(frozen=True, kw_only=True)
class _Greeting:
    text: str


@dataclass(frozen=True, kw_only=True)
class _SayHello(IRequest[_Greeting]):
    name: str


@dataclass(frozen=True, kw_only=True)
class _FireAndForget(IRequest[None]):
    payload: str


@dataclass(frozen=True)
class _SomethingHappened(IEvent):
    detail: str


class _ContextCapturingHandler(RequestHandler[_SayHello, _Greeting]):
    captured: ClassVar[list[MessageContext]] = []

    @override
    async def handle(self, request: _SayHello, /) -> _Greeting:
        self.captured.append(get_message_context())
        return _Greeting(text=f'Hello, {request.name}!')


class _FireAndForgetHandler(RequestHandler[_FireAndForget, None]):
    captured: ClassVar[list[MessageContext]] = []

    @override
    async def handle(self, request: _FireAndForget, /) -> None:
        self.captured.append(get_message_context())


class TestMessagingIntegration:
    @staticmethod
    async def test_invoke_sets_message_context_with_valid_uuid_ids() -> None:
        _ContextCapturingHandler.captured.clear()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_SayHello, _ContextCapturingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            result = await bus.invoke(_SayHello(name='world'))

        assert result == _Greeting(text='Hello, world!')
        assert len(_ContextCapturingHandler.captured) == 1

        ctx = _ContextCapturingHandler.captured[0]
        assert isinstance(ctx.correlation_id, UUID)
        assert isinstance(ctx.causation_id, UUID)
        assert isinstance(ctx.message_id, UUID)

    @staticmethod
    async def test_invoke_context_cleared_after_completion() -> None:
        _ContextCapturingHandler.captured.clear()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_SayHello, _ContextCapturingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_SayHello(name='test'))
            assert try_get_message_context() is None

    @staticmethod
    async def test_invoke_root_message_causation_id_equals_message_id() -> None:
        _ContextCapturingHandler.captured.clear()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_SayHello, _ContextCapturingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_SayHello(name='test'))

        ctx = _ContextCapturingHandler.captured[0]
        assert ctx.causation_id == ctx.message_id

    @staticmethod
    async def test_send_sets_message_context_during_handler() -> None:
        _FireAndForgetHandler.captured.clear()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_FireAndForget, _FireAndForgetHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(_FireAndForget(payload='data'))

        assert len(_FireAndForgetHandler.captured) == 1
        ctx = _FireAndForgetHandler.captured[0]
        assert isinstance(ctx.message_id, UUID)

    @staticmethod
    async def test_publish_sets_message_context_for_each_event_handler() -> None:
        captured_contexts: list[MessageContext] = []

        class HandlerA(EventHandler[_SomethingHappened]):
            @override
            async def handle(self, event: _SomethingHappened, /) -> None:
                captured_contexts.append(get_message_context())

        class HandlerB(EventHandler[_SomethingHappened]):
            @override
            async def handle(self, event: _SomethingHappened, /) -> None:
                captured_contexts.append(get_message_context())

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_event(_SomethingHappened, [HandlerA, HandlerB])],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_SomethingHappened(detail='test'))

        assert len(captured_contexts) == 2
        for ctx in captured_contexts:
            assert isinstance(ctx.message_id, UUID)
            assert isinstance(ctx.correlation_id, UUID)

    @staticmethod
    async def test_distinct_invocations_produce_distinct_message_ids() -> None:
        _ContextCapturingHandler.captured.clear()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_SayHello, _ContextCapturingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_SayHello(name='first'))
            await bus.invoke(_SayHello(name='second'))

        first_ctx, second_ctx = _ContextCapturingHandler.captured
        assert first_ctx.message_id != second_ctx.message_id
        assert first_ctx.correlation_id != second_ctx.correlation_id

    @staticmethod
    async def test_pipeline_behavior_can_access_message_context() -> None:
        captured_in_behavior: list[MessageContext] = []

        class ContextReadingBehavior(IPipelineBehavior[MessageT, ResponseT]):
            @override
            async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
                captured_in_behavior.append(get_message_context())
                return await call_next()

        class SimpleHandler(RequestHandler[_SayHello, _Greeting]):
            @override
            async def handle(self, request: _SayHello, /) -> _Greeting:
                return _Greeting(text=request.name)

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(pipeline_behaviors=[ContextReadingBehavior]))],
                extensions=[MessagingExtension().bind_request(_SayHello, SimpleHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_SayHello(name='test'))

        assert len(captured_in_behavior) == 1
        assert isinstance(captured_in_behavior[0].message_id, UUID)

    @staticmethod
    async def test_nested_invoke_propagates_correlation_id() -> None:
        outer_contexts: list[MessageContext] = []
        inner_contexts: list[MessageContext] = []

        @dataclass(frozen=True, kw_only=True)
        class _InnerRequest(IRequest[None]):
            pass

        class InnerHandler(RequestHandler[_InnerRequest, None]):
            @override
            async def handle(self, request: _InnerRequest, /) -> None:
                inner_contexts.append(get_message_context())

        class OuterHandler(RequestHandler[_SayHello, _Greeting]):
            def __init__(self, bus: IMessageBus) -> None:
                self._bus = bus

            @override
            async def handle(self, request: _SayHello, /) -> _Greeting:
                outer_contexts.append(get_message_context())
                await self._bus.invoke(_InnerRequest())
                return _Greeting(text=request.name)

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension()
                    .bind_request(_SayHello, OuterHandler)
                    .bind_request(_InnerRequest, InnerHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_SayHello(name='nested'))

        assert len(outer_contexts) == 1
        assert len(inner_contexts) == 1

        outer_ctx = outer_contexts[0]
        inner_ctx = inner_contexts[0]

        assert inner_ctx.correlation_id == outer_ctx.correlation_id
        assert inner_ctx.causation_id == outer_ctx.message_id
        assert inner_ctx.message_id != outer_ctx.message_id

    @staticmethod
    async def test_publish_shares_same_context_across_all_handlers() -> None:
        captured_contexts: list[MessageContext] = []

        class HandlerA(EventHandler[_SomethingHappened]):
            @override
            async def handle(self, event: _SomethingHappened, /) -> None:
                captured_contexts.append(get_message_context())

        class HandlerB(EventHandler[_SomethingHappened]):
            @override
            async def handle(self, event: _SomethingHappened, /) -> None:
                captured_contexts.append(get_message_context())

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_event(_SomethingHappened, [HandlerA, HandlerB])],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_SomethingHappened(detail='shared'))

        assert len(captured_contexts) == 2
        assert captured_contexts[0].correlation_id == captured_contexts[1].correlation_id
        assert captured_contexts[0].message_id == captured_contexts[1].message_id

    @staticmethod
    async def test_invoke_clears_context_on_handler_exception() -> None:
        @dataclass(frozen=True, kw_only=True)
        class _FailingRequest(IRequest[None]):
            pass

        class FailingHandler(RequestHandler[_FailingRequest, None]):
            @override
            async def handle(self, request: _FailingRequest, /) -> None:
                msg = 'boom'
                raise RuntimeError(msg)

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind_request(_FailingRequest, FailingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_FailingRequest())
            assert try_get_message_context() is None
