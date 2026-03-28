from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku import module
from waku.di import object_
from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    external_endpoint,
    route,
)
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.testing import create_test_app

from tests.messaging.outbox.fake_store import FakeOutboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _InlineHandler(EventHandler[_OrderPlaced]):
    received: list[str] = []  # noqa: RUF012

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event.order_id)


class TestBusOutboxIntegration:
    @staticmethod
    async def test_publish_routes_event_to_outbox_via_external_endpoint() -> None:
        outbox = FakeOutboxStore()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://events')],
            routing=[route(_OrderPlaced).to('test://events')],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlaced, _InlineHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[object_(outbox, provided_type=IOutboxStore)],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='123'))

        assert len(outbox.saved) == 1
        assert outbox.saved[0].destination == 'test://events'

    @staticmethod
    async def test_send_routes_to_external_endpoint() -> None:
        outbox = FakeOutboxStore()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://events')],
            routing=[route(_OrderPlaced).to('test://events')],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlaced, _InlineHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[object_(outbox, provided_type=IOutboxStore)],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.send(_OrderPlaced(order_id='456'))

        assert len(outbox.saved) == 1
        assert outbox.saved[0].destination == 'test://events'
