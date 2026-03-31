from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.messaging import (
    CallNext,
    IPipelineBehavior,
    IRequest,
    MessageT,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    ResponseT,
)
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.registry import MessageRegistry
from waku.testing import create_test_app


@dataclass(frozen=True, kw_only=True)
class _Ping(IRequest[str]):
    value: str


class _PingHandler(RequestHandler[_Ping, str]):
    @override
    async def handle(self, request: _Ping, /) -> str:
        return f'pong:{request.value}'


async def _invoke(message: _Ping | None = None) -> str:
    msg = message or _Ping(value='test')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_Ping, _PingHandler)],
        ) as app,
        app.container() as scope,
    ):
        registry = await scope.get(MessageRegistry)
        invoker = HandlerPipelineInvoker(registry)
        return await invoker.invoke(scope, msg, _PingHandler)  # type: ignore[no-any-return]


def _make_tracking_behavior(label: str, tracker: list[str]) -> type[IPipelineBehavior[Any, Any]]:
    class _Behavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            tracker.append(label)
            return await call_next()

    _Behavior.__qualname__ = f'_Behavior_{label}'
    _Behavior.__name__ = f'_Behavior_{label}'
    return _Behavior


async def test_invoke_without_behaviors_returns_handler_result() -> None:
    result = await _invoke()
    assert result == 'pong:test'


async def test_invoke_with_global_behaviors_runs_them_before_handler() -> None:
    called: list[str] = []
    global_b = _make_tracking_behavior('global', called)

    ext = MessagingExtension().bind(_Ping, _PingHandler)

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(pipeline_behaviors=[global_b]))],
            extensions=[ext],
        ) as app,
        app.container() as scope,
    ):
        registry = await scope.get(MessageRegistry)
        invoker = HandlerPipelineInvoker(registry)
        result = await invoker.invoke(scope, _Ping(value='gb'), _PingHandler)

    assert result == 'pong:gb'
    assert called == ['global']


async def test_invoke_with_global_and_scoped_behaviors_orders_global_first() -> None:
    called: list[str] = []
    global_b = _make_tracking_behavior('global', called)
    scoped_b = _make_tracking_behavior('scoped', called)

    ext = MessagingExtension().bind(_Ping, _PingHandler, behaviors=[scoped_b])

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(pipeline_behaviors=[global_b]))],
            extensions=[ext],
        ) as app,
        app.container() as scope,
    ):
        registry = await scope.get(MessageRegistry)
        invoker = HandlerPipelineInvoker(registry)
        result = await invoker.invoke(scope, _Ping(value='both'), _PingHandler)

    assert result == 'pong:both'
    assert called == ['global', 'scoped']


async def test_invoke_scoped_behavior_skipped_for_message_without_bindings() -> None:
    called: list[str] = []
    scoped_b = _make_tracking_behavior('scoped', called)

    @dataclass(frozen=True, kw_only=True)
    class OtherRequest(IRequest[str]):
        value: str

    class OtherHandler(RequestHandler[OtherRequest, str]):
        @override
        async def handle(self, request: OtherRequest, /) -> str:
            return f'other:{request.value}'

    ext = MessagingExtension().bind(_Ping, _PingHandler, behaviors=[scoped_b]).bind(OtherRequest, OtherHandler)

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[ext],
        ) as app,
        app.container() as scope,
    ):
        registry = await scope.get(MessageRegistry)
        invoker = HandlerPipelineInvoker(registry)
        result = await invoker.invoke(scope, OtherRequest(value='no-scoped'), OtherHandler)

    assert result == 'other:no-scoped'
    assert called == []
