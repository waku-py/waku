from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from dishka import AsyncContainer
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
from waku.messaging.contracts.event import IEvent as _IEvent
from waku.messaging.endpoints.base import local_queue
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.router import route
from waku.testing import create_test_app

from tests.messaging.helpers import NOOP_EVALUATOR, make_envelope

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.fixture
def noop_executor(mocker: MockerFixture) -> EndpointExecutor:
    return EndpointExecutor(
        container=mocker.Mock(spec_set=AsyncContainer),
        evaluator=NOOP_EVALUATOR,
        endpoint_uri='test://q',
        invoker=mocker.Mock(spec_set=HandlerPipelineInvoker),
        registry=MessageTypeRegistry(identities={}, known_types=[]),
    )


@pytest.fixture
def stopped_endpoint(noop_executor: EndpointExecutor) -> LocalQueueEndpoint:
    return LocalQueueEndpoint(
        uri='test://q',
        handler_subscriptions={},
        executor=noop_executor,
        stop_timeout=1.0,
        max_buffer_size=0,
    )


class TestLocalQueueLifecycle:
    @staticmethod
    async def test_stop_without_start_is_noop(stopped_endpoint: LocalQueueEndpoint) -> None:
        await stopped_endpoint.stop()

    @staticmethod
    async def test_dispatch_to_stopped_endpoint_logs_warning(
        stopped_endpoint: LocalQueueEndpoint,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await stopped_endpoint.start()
        await stopped_endpoint.stop()

        envelope = make_envelope(_IEvent())
        with caplog.at_level(logging.WARNING, logger='waku.messaging.endpoints.local_queue'):
            await stopped_endpoint.dispatch(envelope, mocker.Mock(spec_set=AsyncContainer))

        assert 'Message dropped' in caplog.text

    @staticmethod
    async def test_stop_cancels_slow_worker_on_timeout(caplog: pytest.LogCaptureFixture) -> None:
        blocked = asyncio.Event()
        entered = asyncio.Event()

        @dataclass(frozen=True)
        class SlowEvent(IEvent):
            pass

        class SlowHandler(EventHandler[SlowEvent]):
            @override
            async def handle(self, event: SlowEvent, /) -> None:
                entered.set()
                await blocked.wait()

        config = MessagingConfig(
            endpoints=[local_queue('slow-q', stop_timeout=0.05)],
            routing=[route(SlowEvent).to('slow-q')],
        )

        with caplog.at_level(logging.WARNING, logger='waku.messaging.endpoints.local_queue'):
            async with (
                create_test_app(
                    imports=[MessagingModule.register(config)],
                    extensions=[MessagingExtension().bind(SlowEvent, SlowHandler)],
                ) as app,
                app.container() as container,
            ):
                bus = await container.get(IMessageBus)
                await bus.publish(SlowEvent())
                await asyncio.wait_for(entered.wait(), timeout=1.0)

        assert 'did not terminate within' in caplog.text

    @staticmethod
    async def test_send_routes_request_through_local_queue() -> None:
        received: list[str] = []

        @dataclass(frozen=True)
        class PingRequest(IRequest[None]):
            ping_id: str

        class PingHandler(RequestHandler[PingRequest, None]):
            @override
            async def handle(self, request: PingRequest, /) -> None:
                received.append(request.ping_id)

        config = MessagingConfig(
            endpoints=[local_queue('request-q')],
            routing=[route(PingRequest).to('request-q')],
        )

        @module(extensions=[MessagingExtension().bind(PingRequest, PingHandler)])
        class Mod:
            pass

        async with (
            create_test_app(imports=[MessagingModule.register(config), Mod]) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(PingRequest(ping_id='P-1'))

        assert received == ['P-1']
