from __future__ import annotations

import asyncio
from dataclasses import dataclass

import anyio
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging import (
    EndpointMode,
    EventHandler,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    local_queue,
    route,
)
from waku.testing import create_test_app


async def assert_max_parallel_bounds_concurrency(parallelism: int) -> None:
    """Drive `parallelism` blocking events through a BUFFERED local queue and assert exactly that many run at once."""
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
