from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.messaging import (
    CallNext,
    EventHandler,
    IEvent,
    IPipelineBehavior,
    MessageT,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    ResponseT,
)
from waku.messaging.behaviors.cascading import CascadingBehavior
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.pipeline.policy import BehaviorPlan
from waku.testing import create_test_app


@dataclass(frozen=True, slots=True)
class _Evt(IEvent):
    value: str


def _make_tracking_behavior(label: str, tracker: list[str]) -> type[IPipelineBehavior[Any, Any]]:
    class _Behavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            tracker.append(label)
            return await call_next()

    _Behavior.__qualname__ = f'_Behavior_{label}'
    _Behavior.__name__ = f'_Behavior_{label}'
    return _Behavior


async def test_invoke_without_behaviors_runs_only_handler() -> None:
    called: list[str] = []

    class _H(EventHandler[_Evt]):
        @override
        async def handle(self, message: _Evt, /) -> None:
            called.append('handle')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_H)],
        ) as app,
        app.container() as scope,
    ):
        invoker = await app.container.get(HandlerPipelineInvoker)
        await invoker.invoke(scope, _Evt(value='x'), _H)

    assert called == ['handle']


async def test_global_outer_then_per_handler_inner_then_handle() -> None:
    called: list[str] = []
    global_b = _make_tracking_behavior('global', called)
    per_b = _make_tracking_behavior('per-handler', called)

    class _H(EventHandler[_Evt]):
        behaviors = (per_b,)

        @override
        async def handle(self, message: _Evt, /) -> None:
            called.append('handle')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[global_b]))],
            extensions=[MessagingExtension().bind(_H)],
        ) as app,
        app.container() as scope,
    ):
        invoker = await app.container.get(HandlerPipelineInvoker)
        await invoker.invoke(scope, _Evt(value='x'), _H)

    assert called == ['global', 'per-handler', 'handle']


async def test_handler_without_behaviors_uses_only_global() -> None:
    called: list[str] = []
    global_b = _make_tracking_behavior('global', called)

    class _Bare(EventHandler[_Evt]):
        @override
        async def handle(self, message: _Evt, /) -> None:
            called.append('handle')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[global_b]))],
            extensions=[MessagingExtension().bind(_Bare)],
        ) as app,
        app.container() as scope,
    ):
        invoker = await app.container.get(HandlerPipelineInvoker)
        await invoker.invoke(scope, _Evt(value='x'), _Bare)

    assert called == ['global', 'handle']


async def test_cascading_behavior_is_outermost_in_resolved_chain() -> None:
    # Regression pin: CascadingBehavior must be index 0 (outermost) so its post-commit
    # flush wraps every other behavior, incl. a user TransactionalBehavior's commit.
    global_b = _make_tracking_behavior('global', [])

    class _H(EventHandler[_Evt]):
        @override
        async def handle(self, message: _Evt, /) -> None: ...

    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[global_b]))],
        extensions=[MessagingExtension().bind(_H)],
    ) as app:
        plan = await app.container.get(BehaviorPlan)

    chain = plan.for_handler(_H)
    assert chain[0] is CascadingBehavior
    assert chain[1] is global_b


async def test_two_handlers_of_same_event_have_independent_chains() -> None:
    called: list[str] = []
    per_a = _make_tracking_behavior('a-behavior', called)

    class _HandlerA(EventHandler[_Evt]):
        behaviors = (per_a,)

        @override
        async def handle(self, message: _Evt, /) -> None:
            called.append('a-handle')

    class _HandlerB(EventHandler[_Evt]):
        @override
        async def handle(self, message: _Evt, /) -> None:
            called.append('b-handle')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_HandlerA, _HandlerB)],
        ) as app,
        app.container() as scope,
    ):
        invoker = await app.container.get(HandlerPipelineInvoker)
        await invoker.invoke(scope, _Evt(value='x'), _HandlerA)
        await invoker.invoke(scope, _Evt(value='x'), _HandlerB)

    assert called == ['a-behavior', 'a-handle', 'b-handle']
