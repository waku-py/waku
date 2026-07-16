from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku import UnexpectedRollbackError
from waku._internal.transaction import TransactionExecutionError, TransactionFailureKind
from waku.di import object_, provider
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    HandlerMap,
    IRequest,
    MessageEnvelope,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging._internal.dispatcher import MessageDispatcher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.endpoints import ExecutionOutcome
from waku.messaging.exceptions import HandlerNotFoundError
from waku.messaging.observability.observer import INVOKE_DESTINATION, IMessageObserver, MessageObservers
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

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
            with pytest.raises(HandlerNotFoundError, match='_Cmd'):
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
            with pytest.raises(HandlerNotFoundError, match='_Evt'):
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
    async def test_non_transactional_fanout_never_resolves_uow() -> None:
        called: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(event.value)

        def provide_uow() -> IUnitOfWork:
            msg = 'non-transactional invocation resolved IUnitOfWork'
            raise AssertionError(msg)

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(HandlerA)],
                providers=[provider(provide_uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='direct')))

        assert called == ['direct']

    @staticmethod
    async def test_invoker_reports_transaction_capability_strictly() -> None:
        class DirectHandler(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None: ...

        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(DirectHandler)],
        ) as app:
            invoker = await app.container.get(HandlerPipelineInvoker)

        assert invoker.has_transaction(DirectHandler) is False

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


class _TraceUoW(IUnitOfWork):
    def __init__(
        self,
        trace: list[str],
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self._trace = trace
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    @override
    async def commit(self) -> None:
        self._trace.append('commit')
        if self._commit_error is not None:
            raise self._commit_error

    @override
    async def rollback(self) -> None:
        self._trace.append('rollback')
        if self._rollback_error is not None:
            raise self._rollback_error


class _TracingHookSpy(_HookSpy):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self._trace = trace

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
        self._trace.append(f'executed:{handler_type.__name__}:{outcome.value}')
        await super().on_executed(envelope, destination, handler_type, outcome, exc, duration)


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
                handler_map=await app.container.get(HandlerMap),
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
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert [e[0] for e in spy.events] == ['executing', 'executing', 'executed', 'executed']
        assert {e[2] for e in spy.events} == {'HandlerA', 'HandlerB'}

    @staticmethod
    async def test_transactional_fanout_emits_success_only_after_outer_commit() -> None:
        trace: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-a')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-b')

        uow = _TraceUoW(trace)
        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = MessageDispatcher(
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert trace == [
            'handler-a',
            'handler-b',
            'commit',
            'executed:HandlerA:SUCCESS',
            'executed:HandlerB:SUCCESS',
        ]

    @staticmethod
    async def test_transactional_fanout_commit_failure_reclassifies_every_attempt() -> None:
        trace: list[str] = []
        commit_error = RuntimeError('commit failed')

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-a')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-b')

        uow = _TraceUoW(trace, commit_error=commit_error)
        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = MessageDispatcher(
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(RuntimeError, match='commit failed'):
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert trace[:4] == ['handler-a', 'handler-b', 'commit', 'rollback']
        assert spy.failures == [
            (ExecutionOutcome.FAILED_NO_POLICY, commit_error),
            (ExecutionOutcome.FAILED_NO_POLICY, commit_error),
        ]

    @staticmethod
    async def test_transactional_fanout_rollback_only_reclassifies_every_attempt() -> None:
        trace: list[str] = []
        nested_error = ValueError('nested failed')

        class HandlerA(EventHandler[_Evt]):
            def __init__(self, depth: TransactionDepth) -> None:
                self._depth = depth

            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-a')
                self._depth.mark_rollback_only(nested_error)

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler-b')

        uow = _TraceUoW(trace)
        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = MessageDispatcher(
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(UnexpectedRollbackError) as raised:
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert raised.value.__cause__ is nested_error
        assert trace[:3] == ['handler-a', 'handler-b', 'rollback']
        assert spy.failures == [
            (ExecutionOutcome.FAILED_NO_POLICY, raised.value),
            (ExecutionOutcome.FAILED_NO_POLICY, raised.value),
        ]

    @staticmethod
    async def test_transactional_fanout_rollback_failure_emits_no_terminal_evidence() -> None:
        trace: list[str] = []
        handler_error = ValueError('handler failed')
        rollback_error = RuntimeError('rollback failed')

        class FailingHandler(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler')
                raise handler_error

        uow = _TraceUoW(trace, rollback_error=rollback_error)
        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(FailingHandler)],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = MessageDispatcher(
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(TransactionExecutionError) as raised:
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert raised.value.kind is TransactionFailureKind.ROLLBACK_FAILED
        assert raised.value.error is rollback_error
        assert raised.value.primary_error is handler_error
        assert trace == ['handler', 'rollback']
        assert spy.failures == []

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
                handler_map=await app.container.get(HandlerMap),
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
                handler_map=await app.container.get(HandlerMap),
                invoker=await app.container.get(HandlerPipelineInvoker),
                observers=MessageObservers([spy]),
            )
            with pytest.raises(HandlerNotFoundError, match='_Cmd'):
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='x')))
        assert spy.events == []


class TestDispatchToHandler:
    @staticmethod
    async def test_runs_only_the_named_handler() -> None:
        seen: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
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
            await dispatcher.dispatch_to_handler(container, make_envelope(_Evt(value='replay')), HandlerB)

        assert seen == ['b:replay']

    @staticmethod
    async def test_handler_exception_propagates_without_policy_swallow() -> None:
        class Failing(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                msg = 'boom'
                raise RuntimeError(msg)

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(Failing)],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await app.container.get(MessageDispatcher)
            with pytest.raises(RuntimeError, match='boom'):
                await dispatcher.dispatch_to_handler(container, make_envelope(_Evt(value='x')), Failing)
