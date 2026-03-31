from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku import module
from waku.di import object_
from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    RequestHandler,
    external_endpoint,
)
from waku.messaging.endpoints.base import local_queue
from waku.messaging.endpoints.external import ExternalEndpoint
from waku.messaging.router import MessageRouter, RoutingTable, route
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingTransport
from tests.messaging.outbox.fake_store import FakeOutboxStore


@dataclass(frozen=True)
class _Notif(IEvent):
    notif_id: str


class _DummyNotifHandler(EventHandler[_Notif]):
    @override
    async def handle(self, event: _Notif, /) -> None:
        pass


class TestRoutingBranches:
    @staticmethod
    async def test_external_endpoint_is_created() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('local-q'), external_endpoint('ext://bus')],
            routing=[route(_Notif).to('ext://bus')],
            outbox=OutboxConfig(store=FakeOutboxStore, transport=RecordingTransport),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_Notif, _DummyNotifHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            router = await container.get(MessageRouter)
            ext_endpoints = [e for e in router.endpoints if isinstance(e, ExternalEndpoint)]
            assert len(ext_endpoints) == 1
            assert ext_endpoints[0].uri == 'ext://bus'

    @staticmethod
    async def test_request_route_dispatches_through_endpoint() -> None:
        called: list[str] = []

        @dataclass(frozen=True)
        class Cmd(IRequest[None]):
            cmd_id: str

        class CmdHandler(RequestHandler[Cmd, None]):
            @override
            async def handle(self, request: Cmd, /) -> None:
                called.append(request.cmd_id)

        config = MessagingConfig(
            endpoints=[local_queue('cmd-q')],
            routing=[route(Cmd).to('cmd-q')],
        )

        @module(extensions=[MessagingExtension().bind(Cmd, CmdHandler)])
        class CmdModule:
            pass

        async with (
            create_test_app(imports=[MessagingModule.register(config), CmdModule]) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(Cmd(cmd_id='C-1'))

        assert called == ['C-1']

    @staticmethod
    async def test_endpoint_without_routes_gets_empty_subscriptions() -> None:
        called: list[str] = []

        class NotifHandler(EventHandler[_Notif]):
            @override
            async def handle(self, event: _Notif, /) -> None:
                called.append(event.notif_id)

        config = MessagingConfig(
            endpoints=[local_queue('used-q'), local_queue('unused-q')],
            routing=[route(_Notif).to('used-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_Notif, NotifHandler)],
            ) as app,
            app.container() as container,
        ):
            routing_table = await container.get(RoutingTable)
            assert 'unused-q' not in routing_table.endpoint_subscriptions

            bus = await container.get(IMessageBus)
            await bus.publish(_Notif(notif_id='N-2'))

        assert called == ['N-2']
