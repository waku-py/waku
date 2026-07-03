from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from waku.messaging import EventHandler, IEvent, MessagingExtension, MessagingModule
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.inline import InlineEndpoint
from waku.messaging.observability.observer import MessageObservers
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app

from tests.messaging.helpers import NOOP_EVALUATOR

if TYPE_CHECKING:
    from waku.application import WakuApplication


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[_OrderPlaced]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event)


async def _make_inline_endpoint(app: WakuApplication) -> InlineEndpoint:
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecutor(
        container=app.container,
        evaluator=NOOP_EVALUATOR,
        endpoint_uri='inline://test',
        invoker=invoker,
        observers=MessageObservers([]),
    )
    return InlineEndpoint(
        uri='inline://test',
        handler_subscriptions={_OrderPlaced: frozenset({_RecordingHandler})},
        executor=executor,
    )


class TestInlineEndpointDispatch:
    @staticmethod
    async def test_dispatch_processes_handler_synchronously() -> None:
        _RecordingHandler.received.clear()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
        ) as app:
            endpoint = await _make_inline_endpoint(app)
            envelope = (await app.container.get(EnvelopeFactory)).create(_OrderPlaced(order_id='sync-1'))
            await endpoint.dispatch(envelope, app.container)

        # No start()/stop() needed; handler ran inline during dispatch().
        assert len(_RecordingHandler.received) == 1
        assert _RecordingHandler.received[0].order_id == 'sync-1'

    @staticmethod
    async def test_start_and_stop_are_noops() -> None:
        async with create_test_app(imports=[MessagingModule.register()]) as app:
            endpoint = await _make_inline_endpoint(app)
            await endpoint.start()
            await endpoint.stop()

    @staticmethod
    async def test_dispatch_without_subscription_is_noop() -> None:
        _RecordingHandler.received.clear()

        @dataclass(frozen=True, slots=True)
        class UnrelatedEvent(IEvent):
            pass

        async with create_test_app(imports=[MessagingModule.register()]) as app:
            endpoint = await _make_inline_endpoint(app)
            envelope = (await app.container.get(EnvelopeFactory)).create(UnrelatedEvent())
            await endpoint.dispatch(envelope, app.container)

        assert _RecordingHandler.received == []
