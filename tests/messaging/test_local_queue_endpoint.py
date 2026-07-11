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
from waku.messages import (
    IEvent,
    IEvent as _IEvent,
)
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.circuit_breaker import CircuitBreakerConfig
from waku.messaging.endpoints.base import local_queue
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionResult
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.router import route
from waku.testing import create_test_app

from tests._wait import wait_until
from tests.messaging.helpers import NOOP_EVALUATOR, NOOP_OBSERVERS, make_envelope

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
        observers=MessageObservers([]),
    )


@pytest.fixture
def stopped_endpoint(noop_executor: EndpointExecutor) -> LocalQueueEndpoint:
    return LocalQueueEndpoint(
        uri='test://q',
        handler_subscriptions={},
        executor=noop_executor,
        observers=NOOP_OBSERVERS,
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
                    extensions=[MessagingExtension().bind(SlowHandler)],
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

        @module(extensions=[MessagingExtension().bind(PingHandler)])
        class Mod:
            pass

        async with (
            create_test_app(imports=[MessagingModule.register(config), Mod]) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(PingRequest(ping_id='P-1'))

        assert received == ['P-1']


class _SentSpy(IMessageObserver):
    def __init__(self) -> None:
        self.sent: list[str] = []

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self.sent.append(destination)


class TestLocalQueueOnSent:
    @staticmethod
    async def test_dispatch_fires_on_sent_after_successful_hand_off(
        noop_executor: EndpointExecutor,
        mocker: MockerFixture,
    ) -> None:
        spy = _SentSpy()
        endpoint = LocalQueueEndpoint(
            uri='test://q',
            handler_subscriptions={},
            executor=noop_executor,
            observers=MessageObservers([spy]),
            stop_timeout=1.0,
            max_buffer_size=math.inf,
        )
        await endpoint.start()
        try:
            await endpoint.dispatch(make_envelope(_IEvent()), mocker.Mock(spec_set=AsyncContainer))
            await wait_until(lambda: spy.sent == [endpoint.uri])
        finally:
            await endpoint.stop()

    @staticmethod
    async def test_dispatch_to_stopped_endpoint_does_not_fire_on_sent(
        noop_executor: EndpointExecutor,
        mocker: MockerFixture,
    ) -> None:
        spy = _SentSpy()
        endpoint = LocalQueueEndpoint(
            uri='test://q',
            handler_subscriptions={},
            executor=noop_executor,
            observers=MessageObservers([spy]),
            stop_timeout=1.0,
            max_buffer_size=0,
        )
        # Endpoint never started -> the worker rejects the send, mirroring the stopped path.
        await endpoint.dispatch(make_envelope(_IEvent()), mocker.Mock(spec_set=AsyncContainer))
        assert spy.sent == []


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
                extensions=[MessagingExtension().bind(BlockingHandler)],
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
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        if on_result is not None:
            await on_result(ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
        return ExecutionResult(ExecutionOutcome.FAILED_NO_POLICY)


class TestLocalQueueCircuitBreaker:
    @staticmethod
    async def test_circuit_breaker_trips_and_pauses_processing(mocker: MockerFixture) -> None:
        executor = _AlwaysFailStubExecutor()
        endpoint = LocalQueueEndpoint(
            uri='cb-q',
            handler_subscriptions={_CbEvent: frozenset([_CbHandler])},
            executor=executor,
            observers=NOOP_OBSERVERS,
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
            # After 2 failures the breaker trips → worker halts.
            await wait_until(lambda: executor.calls >= 2)
            # Remaining messages stay enqueued (if CB were absent, all 4 would run).
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert executor.calls == 2
        finally:
            await endpoint.stop()  # aclose()s the CB, cancelling the parked resume (no real time elapsed)
