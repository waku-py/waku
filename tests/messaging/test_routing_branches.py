from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from typing_extensions import override

from waku import module
from waku.di import Provider, object_, scoped
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    RequestHandler,
    TransactionalBehavior,
    external_endpoint,
)
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.endpoints._internal.external import ExternalEndpoint
from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.router import MessageRouter, RoutingTable, listen, local_queue, route
from waku.messaging.transport._internal.registry import TransportRegistry
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import RecordingTransport, RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore


@dataclass(frozen=True)
class _Notif(IEvent):
    notif_id: str


class _DummyNotifHandler(EventHandler[_Notif]):
    @override
    async def handle(self, event: _Notif, /) -> None:
        pass


def _store_providers() -> tuple[Provider, ...]:
    return (scoped(IOutboxStore, RecordingOutboxStore), scoped(IInboxStore, FakeInboxStore))


class TestRoutingBranches:
    @staticmethod
    async def test_external_endpoint_is_created() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('local-q'), external_endpoint('ext://bus')],
            routing=[route(_Notif).to('ext://bus')],
            outbox=OutboxConfig(),
            transports={'ext': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_DummyNotifHandler)],
                providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), *_store_providers()],
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

        @module(extensions=[MessagingExtension().bind(CmdHandler)])
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
                extensions=[MessagingExtension().bind(NotifHandler)],
            ) as app,
            app.container() as container,
        ):
            routing_table = await container.get(RoutingTable)
            assert 'unused-q' not in routing_table.endpoint_subscriptions

            bus = await container.get(IMessageBus)
            await bus.publish(_Notif(notif_id='N-2'))

        assert called == ['N-2']


class _MarkerMapper(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> Any:
        raise NotImplementedError  # pragma: no cover

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], EnvelopeMetadata]:
        raise NotImplementedError  # pragma: no cover


class TestMergedEndpointRegistryProjection:
    @staticmethod
    async def test_mapper_reaches_transport_registry_via_injected_merged_collection() -> None:
        # Proves builders -> merge_broker_endpoints -> object_(merged) -> DI-injected
        # _build_transport_registry -> TransportRegistry.mapper_for, end-to-end through a real container.
        override_mapper = _MarkerMapper()
        config = MessagingConfig(
            endpoints=[external_endpoint('rabbitmq://orders', mapper=override_mapper)],
            outbox=OutboxConfig(),
            transports={'rabbitmq': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), *_store_providers()],
            ) as app,
            app.container() as container,
        ):
            registry = await container.get(TransportRegistry)
            assert registry.mapper_for('rabbitmq://orders') is override_mapper
            assert registry.mapper_for('rabbitmq://unconfigured') is None


class TestMergedEndpointSendRouting:
    @staticmethod
    async def test_routing_table_resolves_merged_broker_endpoint() -> None:
        config = MessagingConfig(
            endpoints=[external_endpoint('rabbitmq://orders')],
            routing=[route(_Notif).to('rabbitmq://orders')],
            outbox=OutboxConfig(),
            transports={'rabbitmq': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_DummyNotifHandler)],
                providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), *_store_providers()],
            ) as app,
            app.container() as container,
        ):
            routing_table = await container.get(RoutingTable)
            matching = [e for e in routing_table.entries if e.uri == 'rabbitmq://orders']
            assert len(matching) == 1
            assert isinstance(matching[0], MergedBrokerEndpoint)

            router = await container.get(MessageRouter)
            endpoint = router.endpoint_for('rabbitmq://orders')
            assert isinstance(endpoint, ExternalEndpoint)
            assert endpoint.uri == 'rabbitmq://orders'


class TestMergedEndpointListenOnlyRouting:
    @staticmethod
    async def test_route_to_listen_only_endpoint_raises() -> None:
        config = MessagingConfig(
            endpoints=[listen('rabbitmq://orders')],
            routing=[route(_Notif).to('rabbitmq://orders')],
            inbox=InboxConfig(owner_id='test-node:1'),
            transports={'rabbitmq': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        with pytest.raises(ImproperlyConfiguredError, match='listen-only'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_DummyNotifHandler)],
                providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), *_store_providers()],
            ):
                pass  # pragma: no cover
