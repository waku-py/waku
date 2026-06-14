from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar

import anyio
from typing_extensions import override

from waku.messaging import (
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
