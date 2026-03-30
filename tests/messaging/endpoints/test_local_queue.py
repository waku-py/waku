from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from typing_extensions import override

from waku.messaging import EventHandler, IEvent, MessagingExtension, MessagingModule
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.testing import create_test_app

from tests.messaging.helpers import NOOP_EVALUATOR


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[_OrderPlaced]] = []
    contexts: ClassVar[list[MessageContext]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event)
        self.contexts.append(get_message_context())


class _FailingThenRecordingHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[_OrderPlaced]] = []
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        type(self).call_count += 1
        if type(self).call_count == 1:
            msg = 'Simulated handler failure'
            raise RuntimeError(msg)
        self.received.append(event)


class TestLocalQueueEndpoint:
    @staticmethod
    async def test_dispatched_event_is_processed_by_handler() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_OrderPlaced, _RecordingHandler)],
        ) as app:
            executor = EndpointExecutor(container=app.container, evaluator=NOOP_EVALUATOR, endpoint_uri='local://test')
            endpoint = LocalQueueEndpoint(
                uri='local://test',
                handler_subscriptions={_OrderPlaced: frozenset({_RecordingHandler})},
                executor=executor,
                stop_timeout=0.5,
                max_buffer_size=100,
            )
            await endpoint.start()
            envelope = EnvelopeFactory.create(_OrderPlaced(order_id='abc-123'))
            await endpoint.dispatch(envelope, app.container)
            await endpoint.stop()

        assert len(_RecordingHandler.received) == 1
        assert _RecordingHandler.received[0].order_id == 'abc-123'

    @staticmethod
    async def test_worker_sets_message_context_during_handler_execution() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_OrderPlaced, _RecordingHandler)],
        ) as app:
            executor = EndpointExecutor(container=app.container, evaluator=NOOP_EVALUATOR, endpoint_uri='local://test')
            endpoint = LocalQueueEndpoint(
                uri='local://test',
                handler_subscriptions={_OrderPlaced: frozenset({_RecordingHandler})},
                executor=executor,
                stop_timeout=0.5,
                max_buffer_size=100,
            )
            await endpoint.start()
            envelope = EnvelopeFactory.create(_OrderPlaced(order_id='ctx-test'))
            await endpoint.dispatch(envelope, app.container)
            await endpoint.stop()

        assert len(_RecordingHandler.contexts) == 1
        ctx = _RecordingHandler.contexts[0]
        assert ctx.correlation_id == envelope.correlation_id
        assert ctx.causation_id == envelope.causation_id
        assert ctx.message_id == envelope.message_id
        assert isinstance(ctx.correlation_id, UUID)

    @staticmethod
    async def test_worker_continues_processing_after_handler_error() -> None:
        _FailingThenRecordingHandler.received.clear()
        _FailingThenRecordingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_OrderPlaced, _FailingThenRecordingHandler)],
        ) as app:
            executor = EndpointExecutor(container=app.container, evaluator=NOOP_EVALUATOR, endpoint_uri='local://test')
            endpoint = LocalQueueEndpoint(
                uri='local://test',
                handler_subscriptions={_OrderPlaced: frozenset({_FailingThenRecordingHandler})},
                executor=executor,
                stop_timeout=0.5,
                max_buffer_size=100,
            )
            await endpoint.start()
            first = EnvelopeFactory.create(_OrderPlaced(order_id='will-fail'))
            second = EnvelopeFactory.create(_OrderPlaced(order_id='will-succeed'))
            await endpoint.dispatch(first, app.container)
            await endpoint.dispatch(second, app.container)
            await endpoint.stop()

        assert _FailingThenRecordingHandler.call_count == 2
        assert len(_FailingThenRecordingHandler.received) == 1
        assert _FailingThenRecordingHandler.received[0].order_id == 'will-succeed'
