from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku import module
from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.endpoints.base import EndpointEntry, EndpointKind, local_queue
from waku.messaging.router import MessageRouter, route
from waku.testing import create_test_app


@dataclass(frozen=True)
class _Notif(IEvent):
    notif_id: str


class TestRoutingBranches:
    @staticmethod
    async def test_external_endpoint_kind_is_skipped_in_router_creation() -> None:
        called: list[str] = []

        class NotifHandler(EventHandler[_Notif]):
            @override
            async def handle(self, event: _Notif, /) -> None:
                called.append(event.notif_id)

        external_entry = EndpointEntry(uri='ext://bus', kind=EndpointKind.EXTERNAL)

        config = MessagingConfig(
            endpoints=[local_queue('local-q'), external_entry],
            routing=[route(_Notif).to('local-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind_event(_Notif, [NotifHandler])],
            ) as app,
            app.container() as container,
        ):
            router = await container.get(MessageRouter)
            endpoint_uris = [ep.uri for ep in router.endpoints]
            assert 'ext://bus' not in endpoint_uris
            assert 'local-q' in endpoint_uris

            bus = await container.get(IMessageBus)
            await bus.publish(_Notif(notif_id='N-1'))

        assert called == ['N-1']

    @staticmethod
    async def test_request_route_does_not_populate_handler_routes() -> None:
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

        @module(extensions=[MessagingExtension().bind_request(Cmd, CmdHandler)])
        class CmdModule:
            pass

        async with (
            create_test_app(imports=[MessagingModule.register(config), CmdModule]) as app,
            app.container() as container,
        ):
            router = await container.get(MessageRouter)
            assert router.routed_handler_types(Cmd) == frozenset()

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
                extensions=[MessagingExtension().bind_event(_Notif, [NotifHandler])],
            ) as app,
            app.container() as container,
        ):
            router = await container.get(MessageRouter)
            unused_ep = next(ep for ep in router.endpoints if ep.uri == 'unused-q')
            assert unused_ep.handler_subscriptions == {}

            bus = await container.get(IMessageBus)
            await bus.publish(_Notif(notif_id='N-2'))

        assert called == ['N-2']
