from __future__ import annotations

from dataclasses import dataclass

import pytest
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
from waku.messaging.endpoints.base import ExternalEntry, local_queue
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.router import RoutingTable, route
from waku.testing import create_test_app


@dataclass(frozen=True)
class _Notif(IEvent):
    notif_id: str


class TestRoutingBranches:
    @staticmethod
    async def test_external_endpoint_raises_not_supported() -> None:
        external_entry = ExternalEntry(uri='ext://bus')

        config = MessagingConfig(
            endpoints=[local_queue('local-q'), external_entry],
        )

        with pytest.raises(ImproperlyConfiguredError, match='External endpoints are not yet supported'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

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
