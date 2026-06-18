from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

import anyio.lowlevel
from typing_extensions import override

from waku.messaging import (
    CircuitBreakerConfig,
    EndpointMode,
    EventHandler,
    IEvent,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    local_queue,
    route,
)
from waku.testing import create_test_app

from tests.messaging.helpers import wait_until


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _SyncHandler(EventHandler[_OrderPlaced]):
    observed_from_publish: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.observed_from_publish.append(event.order_id)


class TestInlineModeEndToEnd:
    @staticmethod
    async def test_inline_endpoint_invokes_handler_before_publish_returns() -> None:
        _SyncHandler.observed_from_publish.clear()

        config = MessagingConfig(
            endpoints=[local_queue('inline-q', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('inline-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_OrderPlaced, _SyncHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='sync-1'))
            # No awaits or sleeps — handler must have already run in caller's scope.
            assert _SyncHandler.observed_from_publish == ['sync-1']


class TestBufferedModeConcurrencyEndToEnd:
    @staticmethod
    async def test_buffered_with_max_parallel_runs_handlers_concurrently() -> None:
        parallelism = 5
        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()
        all_started = asyncio.Event()
        release = asyncio.Event()

        @dataclass(frozen=True)
        class WorkEvent(IEvent):
            tag: str

        class BlockingHandler(EventHandler[WorkEvent]):
            @override
            async def handle(self, event: WorkEvent, /) -> None:
                nonlocal in_flight, max_observed
                async with lock:
                    in_flight += 1
                    max_observed = max(max_observed, in_flight)
                    if in_flight == parallelism:
                        all_started.set()
                await release.wait()
                async with lock:
                    in_flight -= 1

        config = MessagingConfig(
            endpoints=[local_queue('work-q', mode=EndpointMode.BUFFERED, max_parallel=parallelism)],
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


class TestCircuitBreakerEndToEnd:
    @staticmethod
    async def test_buffered_breaker_trips_and_halts_processing() -> None:
        handled: list[int] = []

        @dataclass(frozen=True)
        class _Boom(IEvent):
            n: int

        class _FailingHandler(EventHandler[_Boom]):
            @override
            async def handle(self, event: _Boom, /) -> None:
                handled.append(event.n)
                msg = 'boom'
                raise RuntimeError(msg)

        config = MessagingConfig(
            endpoints=[
                local_queue(
                    'cb-q',
                    mode=EndpointMode.BUFFERED,
                    circuit_breaker=CircuitBreakerConfig(
                        minimum_throughput=2,
                        failure_rate_threshold=0.5,
                        pause_time=timedelta(minutes=5),  # large: the timed resume must NOT fire during the test
                    ),
                ),
            ],
            routing=[route(_Boom).to('cb-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_Boom, _FailingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            for i in range(4):
                await bus.publish(_Boom(n=i))
            # Each handler raises → FAILED_NO_POLICY → the breaker records a failure. After
            # minimum_throughput=2 failures it trips → pause() halts the BUFFERED worker; the remaining
            # 2 messages stay buffered, unprocessed. (If _create_endpoint did NOT thread the CB, all 4
            # would process and len(handled) would reach 4.)
            await wait_until(lambda: len(handled) >= 2)
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert len(handled) == 2

    @staticmethod
    async def test_default_circuit_breaker_applies_when_endpoint_declares_none() -> None:
        handled: list[int] = []

        @dataclass(frozen=True)
        class _Bang(IEvent):
            n: int

        class _FailingHandler(EventHandler[_Bang]):
            @override
            async def handle(self, event: _Bang, /) -> None:
                handled.append(event.n)
                msg = 'bang'
                raise RuntimeError(msg)

        config = MessagingConfig(
            default_circuit_breaker=CircuitBreakerConfig(
                minimum_throughput=2,
                failure_rate_threshold=0.5,
                pause_time=timedelta(minutes=5),  # large: the timed resume must NOT fire during the test
            ),
            endpoints=[local_queue('cb-default-q', mode=EndpointMode.BUFFERED)],
            routing=[route(_Bang).to('cb-default-q')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_Bang, _FailingHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            for i in range(4):
                await bus.publish(_Bang(n=i))
            # The endpoint declares no circuit_breaker, so the fallback default_circuit_breaker must apply.
            # After 2 failures it trips → pause() halts the worker; the remaining 2 stay buffered.
            await wait_until(lambda: len(handled) >= 2)
            for _ in range(10):
                await anyio.lowlevel.checkpoint()
            assert len(handled) == 2
