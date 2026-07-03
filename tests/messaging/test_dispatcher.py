from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.messaging import (
    EventHandler,
    ExecutionOutcome,
    IEvent,
    IRequest,
    MessageEnvelope,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.exceptions import HandlerNotFound
from waku.messaging.observability.observer import INVOKE_DESTINATION, IMessageObserver, MessageObservers
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.registry import MessageRegistry
from waku.testing import create_test_app

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from datetime import timedelta

    from waku.messaging.contracts.handler import HandlerType


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
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='x')))

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
            await dispatcher.invoke_request(container, make_envelope(_Cmd(value='hello')))

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
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='x')))

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
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='hello')))

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
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='x')))

        assert seen == ['failing']


class _HookSpy(IMessageObserver):
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []  # (event, destination, handler-name)
        self.failures: list[tuple[ExecutionOutcome, Exception | None]] = []

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self.events.append(('executing', destination, handler_type.__name__))

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self.events.append(('executed', destination, handler_type.__name__))
        self.failures.append((outcome, exc))


class TestInvokeObservability:
    @staticmethod
    async def test_invoke_request_fires_executing_then_executed_success() -> None:
        class CmdHandler(RequestHandler[_Cmd, None]):
            @override
            async def handle(self, request: _Cmd, /) -> None: ...

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(CmdHandler)],
            ) as app,
            app.container() as container,
        ):
            spy = _HookSpy()
            dispatcher = MessageDispatcher(
                registry=await app.container.get(MessageRegistry),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            await dispatcher.invoke_request(container, make_envelope(_Cmd(value='x')))
        assert spy.events == [
            ('executing', INVOKE_DESTINATION, 'CmdHandler'),
            ('executed', INVOKE_DESTINATION, 'CmdHandler'),
        ]
        assert spy.failures == [(ExecutionOutcome.SUCCESS, None)]

    @staticmethod
    async def test_invoke_event_fires_hooks_per_handler() -> None:
        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None: ...

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None: ...

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
            ) as app,
            app.container() as container,
        ):
            spy = _HookSpy()
            dispatcher = MessageDispatcher(
                registry=await app.container.get(MessageRegistry),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert [e[0] for e in spy.events] == ['executing', 'executed', 'executing', 'executed']
        assert {e[2] for e in spy.events} == {'HandlerA', 'HandlerB'}

    @staticmethod
    async def test_invoke_failure_fires_executed_failed_no_policy_and_reraises() -> None:
        class FailingHandler(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                msg = 'boom'
                raise RuntimeError(msg)

        class NeverRuns(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
                pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(FailingHandler, NeverRuns)],
            ) as app,
            app.container() as container,
        ):
            spy = _HookSpy()
            dispatcher = MessageDispatcher(
                registry=await app.container.get(MessageRegistry),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(RuntimeError, match='boom'):
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert spy.events == [
            ('executing', INVOKE_DESTINATION, 'FailingHandler'),
            ('executed', INVOKE_DESTINATION, 'FailingHandler'),
        ]
        outcome, exc = spy.failures[0]
        assert outcome is ExecutionOutcome.FAILED_NO_POLICY
        assert isinstance(exc, RuntimeError)  # fail-fast: the second handler got NO hooks

    @staticmethod
    async def test_handler_not_found_fires_no_hooks() -> None:
        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as container,
        ):
            spy = _HookSpy()
            dispatcher = MessageDispatcher(
                registry=await app.container.get(MessageRegistry),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(HandlerNotFound, match='_Cmd'):
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='x')))
        assert spy.events == []
