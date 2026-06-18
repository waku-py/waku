from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anyio.lowlevel
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
from waku.messaging.circuit_breaker import CircuitBreakerConfig
from waku.messaging.contracts.event import IEvent as _IEvent
from waku.messaging.endpoints.base import local_queue
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.router import route
from waku.testing import create_test_app

from tests.messaging.helpers import NOOP_EVALUATOR, make_envelope, wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pytest_mock import MockerFixture

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType


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

        with caplog.at_level(logging.WARNING, logger='waku.messaging.endpoints.worker'):
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


class TestLocalQueueConcurrency:
    @staticmethod
    async def test_max_parallel_five_processes_events_concurrently() -> None:
        parallelism = 5
        in_flight = 0
        max_observed = 0
        count_lock = asyncio.Lock()
        all_started = asyncio.Event()
        release = asyncio.Event()

        @dataclass(frozen=True)
        class WorkEvent(IEvent):
            tag: str

        class BlockingHandler(EventHandler[WorkEvent]):
            @override
            async def handle(self, event: WorkEvent, /) -> None:
                nonlocal in_flight, max_observed
                async with count_lock:
                    in_flight += 1
                    max_observed = max(max_observed, in_flight)
                    if in_flight == parallelism:
                        all_started.set()
                await release.wait()
                async with count_lock:
                    in_flight -= 1

        config = MessagingConfig(
            endpoints=[local_queue('work-q', max_parallel=parallelism)],
            routing=[route(WorkEvent).to('work-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(WorkEvent, BlockingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            for i in range(parallelism):
                await bus.publish(WorkEvent(tag=str(i)))
            with anyio.fail_after(5):
                await all_started.wait()
            release.set()

        assert max_observed == parallelism


@dataclass(frozen=True)
class _CbEvent(IEvent):
    pass


class _CbHandler(EventHandler[_CbEvent]):
    @override
    async def handle(self, event: _CbEvent, /) -> None: ...


class _AlwaysFailStubExecutor(EndpointExecutor):
    def __init__(self) -> None:
        # Bypass parent __init__: this stub does not exercise real dispatch.
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionOutcome:
        self.calls += 1
        if on_result is not None:
            await on_result(ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
        return ExecutionOutcome.FAILED_NO_POLICY


class TestLocalQueueCircuitBreaker:
    @staticmethod
    async def test_circuit_breaker_trips_and_pauses_processing(mocker: MockerFixture) -> None:
        executor = _AlwaysFailStubExecutor()
        endpoint = LocalQueueEndpoint(
            uri='cb-q',
            handler_subscriptions={_CbEvent: frozenset([_CbHandler])},
            executor=executor,
            stop_timeout=1.0,
            max_buffer_size=math.inf,
            circuit_breaker_config=CircuitBreakerConfig(
                minimum_throughput=2,
                failure_rate_threshold=0.5,
                pause_time=timedelta(minutes=5),  # large: the timed resume must NOT fire during the test
            ),
        )
        await endpoint.start()
        try:
            scope = mocker.Mock(spec_set=AsyncContainer)
            for _ in range(4):
                await endpoint.dispatch(make_envelope(_CbEvent()), scope)
            # After minimum_throughput=2 failures the breaker trips → it calls pause() → the worker halts.
            await wait_until(lambda: executor.calls >= 2)
            # Plateau: the remaining messages stay enqueued, UNprocessed, because the worker is paused.
            # (If the CB were not wired, all 4 would process and calls would reach 4.)
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert executor.calls == 2
        finally:
            await endpoint.stop()  # aclose()s the CB, cancelling the parked resume (no real time elapsed)
