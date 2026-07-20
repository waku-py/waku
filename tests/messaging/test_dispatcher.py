from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku import UnexpectedRollbackError
from waku._internal.transaction import AfterCommitError, RollbackFailedError, TransactionExecutionError
from waku.di import object_, provider
from waku.messages import IEvent
from waku.messaging import (
    EndpointMode,
    EventHandler,
    HandlerMap,
    IOutgoingMessages,
    IRequest,
    MessageEnvelope,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    local_queue,
    route,
)
from waku.messaging._internal.dispatcher import MessageDispatcher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.endpoints import ExecutionOutcome
from waku.messaging.endpoints._internal.inline import InlineEndpoint
from waku.messaging.exceptions import HandlerNotFoundError
from waku.messaging.observability.observer import INVOKE_DESTINATION, IMessageObserver, MessageObservers
from waku.messaging.outgoing import IOutgoingMessagesFrames
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from datetime import timedelta

    from dishka import AsyncContainer
    from pytest_mock import MockerFixture

    from waku.messaging.contracts.handler import HandlerType


@dataclass(frozen=True, kw_only=True)
class _Cmd(IRequest[None]):
    value: str


@dataclass(frozen=True, kw_only=True)
class _Evt(IEvent):
    value: str


@dataclass(frozen=True, kw_only=True)
class _Query(IRequest[str]):
    value: str


@dataclass(frozen=True, kw_only=True)
class _Cascade(IEvent):
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


async def _observing_dispatcher(app_container: AsyncContainer, spy: IMessageObserver) -> MessageDispatcher:
    return MessageDispatcher(
        handler_map=await app_container.get(HandlerMap),
        invoker=await app_container.get(HandlerPipelineInvoker),
        observers=MessageObservers([spy]),
    )


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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(TransactionExecutionError) as raised:
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert isinstance(raised.value, RollbackFailedError)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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
            dispatcher = await _observing_dispatcher(app.container, spy)
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


def _lifecycle_config(*, transactional: bool) -> MessagingConfig:
    # INLINE keeps the post-commit cascade flush synchronous, so the trace records its exact position
    # relative to commit and to the terminal `executed` observation.
    return MessagingConfig(
        endpoints=[local_queue('local://cascade', mode=EndpointMode.INLINE)],
        routing=[route(_Cascade).to('local://cascade')],
        global_pipeline_behaviors=[TransactionalBehavior] if transactional else [],
    )


class _CascadingCmdHandler(RequestHandler[_Cmd, None]):
    def __init__(self, outgoing: IOutgoingMessages, trace: list[str]) -> None:
        self._outgoing = outgoing
        self._trace = trace

    @override
    async def handle(self, request: _Cmd, /) -> None:
        self._trace.append('handler')
        self._outgoing.publish(_Cascade(value=request.value))


class _CascadingEvtHandler(EventHandler[_Evt]):
    def __init__(self, outgoing: IOutgoingMessages, trace: list[str]) -> None:
        self._outgoing = outgoing
        self._trace = trace

    @override
    async def handle(self, event: _Evt, /) -> None:
        self._trace.append('handler')
        self._outgoing.publish(_Cascade(value=event.value))


class _TracingCascadeHandler(EventHandler[_Cascade]):
    def __init__(self, trace: list[str]) -> None:
        self._trace = trace

    @override
    async def handle(self, event: _Cascade, /) -> None:
        self._trace.append('cascade')


class TestDispatcherLifecycle:
    # These cases pin the OBSERVABLE lifecycle contract (result identity, cascade/commit/observation
    # ordering, failure algebra). On the transactional cases `commit` can reach the trace from the
    # per-handler `TransactionalBehavior` frame alone, so they do not by themselves prove the
    # dispatcher owns a frame: that is pinned by `TestDispatcherFrameOrdering` (frame membership at
    # `executing`) and by the flush-failure pair below (`AfterCommitError` vs unwrapped), which only
    # the dispatcher-owned lifecycle can produce.
    @staticmethod
    async def test_transactional_request_preserves_non_none_result_and_commits_before_success() -> None:
        trace: list[str] = []

        class QueryHandler(RequestHandler[_Query, str]):
            @override
            async def handle(self, request: _Query, /) -> str:
                trace.append('handler')
                return f'answer:{request.value}'

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(QueryHandler)],
                providers=[object_(_TraceUoW(trace), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            result = await dispatcher.invoke_request(container, make_envelope(_Query(value='q')))

        assert result == 'answer:q'
        assert trace == ['handler', 'commit', 'executed:QueryHandler:SUCCESS']

    @staticmethod
    async def test_transactional_request_flushes_cascade_after_commit_and_before_success() -> None:
        trace: list[str] = []

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=True))],
                extensions=[MessagingExtension().bind(_CascadingCmdHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert trace.index('handler') < trace.index('commit') < trace.index('cascade')
        assert trace[-1] == 'executed:_CascadingCmdHandler:SUCCESS'

    @staticmethod
    async def test_direct_request_flushes_cascade_before_success() -> None:
        trace: list[str] = []

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=False))],
                extensions=[MessagingExtension().bind(_CascadingCmdHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert trace == ['handler', 'cascade', 'executed:_CascadingCmdHandler:SUCCESS']

    @staticmethod
    async def test_transactional_post_commit_flush_failure_is_after_commit_error_without_terminal_success(
        mocker: MockerFixture,
    ) -> None:
        trace: list[str] = []
        mocker.patch.object(
            InlineEndpoint,
            'dispatch',
            new_callable=mocker.AsyncMock,
            side_effect=RuntimeError('queue unavailable'),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=True))],
                extensions=[MessagingExtension().bind(_CascadingCmdHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(TransactionExecutionError) as raised:
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert isinstance(raised.value, AfterCommitError)
        assert str(raised.value.error) == 'queue unavailable'
        assert trace == ['handler', 'commit']
        assert spy.failures == []

    @staticmethod
    async def test_direct_flush_failure_stays_unwrapped_without_terminal_success(mocker: MockerFixture) -> None:
        trace: list[str] = []
        mocker.patch.object(
            InlineEndpoint,
            'dispatch',
            new_callable=mocker.AsyncMock,
            side_effect=RuntimeError('queue unavailable'),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=False))],
                extensions=[MessagingExtension().bind(_CascadingCmdHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(RuntimeError, match='queue unavailable') as raised:
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert not isinstance(raised.value, TransactionExecutionError)
        assert trace == ['handler']
        assert spy.failures == []

    @staticmethod
    @pytest.mark.parametrize('transactional', [False, True], ids=['direct', 'transactional'])
    async def test_fanout_failure_discards_deferred_cascades(*, transactional: bool) -> None:
        trace: list[str] = []

        class Failing(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                msg = 'boom'
                raise RuntimeError(msg)

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=transactional))],
                extensions=[
                    MessagingExtension().bind(_CascadingEvtHandler, Failing).bind(_TracingCascadeHandler),
                ],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(RuntimeError, match='boom'):
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

            # The owner frame is emptied, not merely left unflushed: nothing survives the failure to
            # leak into a later flush on the same scope.
            outgoing: IOutgoingMessagesFrames = await container.get(IOutgoingMessagesFrames)
            assert not outgoing.detach_deferred()

        assert 'cascade' not in trace

    @staticmethod
    async def test_direct_fanout_cancellation_keeps_identity_and_retains_completed_outcomes() -> None:
        trace: list[str] = []
        cancelled = asyncio.CancelledError()

        class Cancelling(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                raise cancelled

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=False))],
                extensions=[
                    MessagingExtension().bind(_CascadingEvtHandler, Cancelling).bind(_TracingCascadeHandler),
                ],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(asyncio.CancelledError) as raised:
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert raised.value is cancelled
        assert 'cascade' not in trace
        # Direct failure law: the handler that genuinely completed keeps its own outcome; the cancelled
        # one produced no observation at all.
        assert spy.failures == [(ExecutionOutcome.SUCCESS, None)]

    @staticmethod
    async def test_transactional_fanout_cancellation_keeps_identity_and_publishes_no_terminal_evidence() -> None:
        trace: list[str] = []
        cancelled = asyncio.CancelledError()

        class Cancelling(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                raise cancelled

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=True))],
                extensions=[
                    MessagingExtension().bind(_CascadingEvtHandler, Cancelling).bind(_TracingCascadeHandler),
                ],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(asyncio.CancelledError) as raised:
                await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert raised.value is cancelled
        assert trace == ['handler', 'rollback']
        assert spy.failures == []

    @staticmethod
    @pytest.mark.parametrize('transactional', [False, True], ids=['direct', 'transactional'])
    async def test_single_handler_cancellation_keeps_identity_and_publishes_no_terminal_evidence(
        *, transactional: bool
    ) -> None:
        trace: list[str] = []
        cancelled = asyncio.CancelledError()

        class Cancelling(EventHandler[_Evt]):
            def __init__(self, outgoing: IOutgoingMessages, trace: list[str]) -> None:
                self._outgoing = outgoing
                self._trace = trace

            @override
            async def handle(self, event: _Evt, /) -> None:
                self._trace.append('handler')
                self._outgoing.publish(_Cascade(value=event.value))
                raise cancelled

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=transactional))],
                extensions=[MessagingExtension().bind(Cancelling).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(asyncio.CancelledError) as raised:
                await dispatcher.dispatch_to_handler(container, make_envelope(_Evt(value='e')), Cancelling)

        assert raised.value is cancelled
        assert 'cascade' not in trace
        assert spy.failures == []

    @staticmethod
    async def test_transactional_request_commit_failure_discards_cascade_and_reclassifies_the_attempt() -> None:
        trace: list[str] = []
        commit_error = RuntimeError('commit failed')

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=True))],
                extensions=[MessagingExtension().bind(_CascadingCmdHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace, commit_error=commit_error), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            with pytest.raises(RuntimeError, match='commit failed'):
                await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert trace == ['handler', 'commit', 'rollback', 'executed:_CascadingCmdHandler:FAILED_NO_POLICY']
        assert spy.failures == [(ExecutionOutcome.FAILED_NO_POLICY, commit_error)]

    @staticmethod
    @pytest.mark.parametrize('transactional', [False, True], ids=['direct', 'transactional'])
    async def test_dispatch_to_handler_flushes_cascade_before_success(*, transactional: bool) -> None:
        trace: list[str] = []

        async with (
            create_test_app(
                imports=[MessagingModule.register(_lifecycle_config(transactional=transactional))],
                extensions=[MessagingExtension().bind(_CascadingEvtHandler).bind(_TracingCascadeHandler)],
                providers=[
                    object_(_TraceUoW(trace), provided_type=IUnitOfWork),
                    object_(trace, provided_type=list[str]),
                ],
            ) as app,
            app.container() as container,
        ):
            spy = _TracingHookSpy(trace)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.dispatch_to_handler(container, make_envelope(_Evt(value='e')), _CascadingEvtHandler)

        assert trace.index('handler') < trace.index('cascade')
        assert trace[-1] == 'executed:_CascadingEvtHandler:SUCCESS'


class _FrameDepthHookSpy(_TracingHookSpy):
    def __init__(self, trace: list[str], scope: AsyncContainer) -> None:
        super().__init__(trace)
        self._scope = scope

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        depth = await self._scope.get(TransactionDepth)
        self._trace.append(f'executing:{handler_type.__name__}:depth={depth.depth}')
        await super().on_executing(envelope, destination, handler_type)


class TestDispatcherFrameOrdering:
    # One lifecycle authority: the dispatcher's own transaction frame is already OPEN when the
    # per-handler observation window opens, identically for a single handler, a fan-out, and a
    # replay. `decide_transaction` runs `depth.enter()` before the body, so reading the depth from
    # inside `on_executing` discriminates directly on frame membership: depth 1 under the dispatcher
    # frame, depth 0 if the hook ever fires outside it (the nested per-handler TransactionalBehavior
    # only reaches depth 2, and only later, inside the pipeline invocation).
    @staticmethod
    async def test_transactional_request_opens_the_executing_observation_inside_the_frame() -> None:
        trace: list[str] = []

        class CmdHandler(RequestHandler[_Cmd, None]):
            @override
            async def handle(self, request: _Cmd, /) -> None:
                trace.append('handler')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(CmdHandler)],
                providers=[object_(_TraceUoW(trace), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _FrameDepthHookSpy(trace, container)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.invoke_request(container, make_envelope(_Cmd(value='c')))

        assert trace == [
            'executing:CmdHandler:depth=1',
            'handler',
            'commit',
            'executed:CmdHandler:SUCCESS',
        ]

    @staticmethod
    async def test_transactional_fanout_opens_the_executing_observation_inside_the_frame() -> None:
        trace: list[str] = []

        class EvtHandler(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(EvtHandler)],
                providers=[object_(_TraceUoW(trace), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _FrameDepthHookSpy(trace, container)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.invoke_event(container, make_envelope(_Evt(value='e')))

        assert trace == [
            'executing:EvtHandler:depth=1',
            'handler',
            'commit',
            'executed:EvtHandler:SUCCESS',
        ]

    @staticmethod
    async def test_transactional_replay_opens_the_executing_observation_inside_the_frame() -> None:
        trace: list[str] = []

        class EvtHandler(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                trace.append('handler')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(EvtHandler)],
                providers=[object_(_TraceUoW(trace), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            spy = _FrameDepthHookSpy(trace, container)
            dispatcher = await _observing_dispatcher(app.container, spy)
            await dispatcher.dispatch_to_handler(container, make_envelope(_Evt(value='e')), EvtHandler)

        assert trace == [
            'executing:EvtHandler:depth=1',
            'handler',
            'commit',
            'executed:EvtHandler:SUCCESS',
        ]
