from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest
from typing_extensions import override

from waku import module
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
)
from waku.messaging.endpoints.base import local_queue
from waku.messaging.router import route, route_module
from waku.testing import create_test_app


@dataclass(frozen=True)
class _OrderPlaced(IEvent):
    order_id: str


@dataclass(frozen=True)
class _PaymentReceived(IEvent):
    payment_id: str


class _QueuedOrderHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event.order_id)


class _QueuedPaymentHandler(EventHandler[_PaymentReceived]):
    received: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _PaymentReceived, /) -> None:
        self.received.append(event.payment_id)


class _DefaultQueueOrderHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event.order_id)


class TestModuleRouting:
    @staticmethod
    async def test_route_to_unknown_endpoint_raises_error() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('domain-events')],
            routing=[route(_OrderPlaced).to('nonexistent')],
        )

        with pytest.raises(ImproperlyConfiguredError, match='nonexistent'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_QueuedOrderHandler)],
            ):
                pass  # pragma: no cover

    @staticmethod
    async def test_per_type_route_dispatches_event_through_local_queue_endpoint() -> None:
        _QueuedOrderHandler.received.clear()

        config = MessagingConfig(
            endpoints=[local_queue('domain-events')],
            routing=[route(_OrderPlaced).to('domain-events')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_QueuedOrderHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='ORD-1'))

        assert _QueuedOrderHandler.received == ['ORD-1']

    @staticmethod
    async def test_module_route_dispatches_all_module_events_through_endpoint() -> None:
        _QueuedOrderHandler.received.clear()
        _QueuedPaymentHandler.received.clear()

        @module(
            extensions=[
                MessagingExtension().bind(_QueuedOrderHandler).bind(_QueuedPaymentHandler),
            ],
        )
        class DomainModule:
            pass

        config = MessagingConfig(
            endpoints=[local_queue('domain-events')],
            routing=[route_module(DomainModule).to('domain-events')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), DomainModule],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='ORD-10'))
            await bus.publish(_PaymentReceived(payment_id='PAY-20'))

        assert _QueuedOrderHandler.received == ['ORD-10']
        assert _QueuedPaymentHandler.received == ['PAY-20']

    @staticmethod
    async def test_unrouted_event_dispatches_through_default_endpoint() -> None:
        called: list[str] = []

        class InlineHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                called.append(event.order_id)

        config = MessagingConfig(
            endpoints=[local_queue('unused-queue')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(InlineHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='ORD-INLINE'))

        assert called == ['ORD-INLINE']

    @staticmethod
    async def test_partially_routed_event_runs_unrouted_handlers_via_default_endpoint() -> None:
        _QueuedOrderHandler.received.clear()
        _DefaultQueueOrderHandler.received.clear()

        @module(
            extensions=[MessagingExtension().bind(_QueuedOrderHandler)],
        )
        class QueuedModule:
            pass

        @module(
            extensions=[MessagingExtension().bind(_DefaultQueueOrderHandler)],
        )
        class DefaultQueueModule:
            pass

        config = MessagingConfig(
            endpoints=[local_queue('order-events')],
            routing=[route_module(QueuedModule).to('order-events')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), QueuedModule, DefaultQueueModule],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='ORD-BOTH'))

        assert _DefaultQueueOrderHandler.received == ['ORD-BOTH']
        assert _QueuedOrderHandler.received == ['ORD-BOTH']
