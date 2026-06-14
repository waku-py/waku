from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anyio
from anyio.lowlevel import checkpoint

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.message import IMessage
from waku.messaging.endpoints.worker import MemoryStreamWorker

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True, slots=True)
class _Ping(IMessage):
    tag: str


class TestMemoryStreamWorkerLifecycle:
    @staticmethod
    async def test_send_before_start_returns_false() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=0.5)

        accepted = await worker.send(make_envelope(_Ping(tag='a')))

        assert accepted is False

    @staticmethod
    async def test_start_then_send_routes_envelope_to_handler() -> None:
        received: list[str] = []
        processed = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            received.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=0.5)
        await worker.start(handler)
        accepted = await worker.send(make_envelope(_Ping(tag='a')))
        await processed.wait()
        await worker.stop()

        assert accepted is True
        assert received == ['a']

    @staticmethod
    async def test_stop_without_start_is_noop() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=0.5)

        await worker.stop()

    @staticmethod
    async def test_send_after_stop_returns_false() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=0.5)
        await worker.start(_noop)
        await worker.stop()

        accepted = await worker.send(make_envelope(_Ping(tag='a')))

        assert accepted is False


async def _noop(_envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
    return  # pragma: no cover


class TestMemoryStreamWorkerConcurrency:
    @staticmethod
    async def test_max_parallel_one_serializes_processing() -> None:
        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: ARG001
            nonlocal in_flight, max_observed
            async with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
            started.set()
            await release.wait()
            async with lock:
                in_flight -= 1

        worker = MemoryStreamWorker(max_buffer_size=10, stop_timeout=1.0, max_parallel=1)
        await worker.start(handler)
        for i in range(5):
            await worker.send(make_envelope(_Ping(tag=str(i))))
        with anyio.fail_after(5):
            await started.wait()
        release.set()
        await worker.stop()

        assert max_observed == 1

    @staticmethod
    async def test_max_parallel_bounds_concurrency_above_and_below() -> None:
        parallelism = 5
        overflow = parallelism + 3
        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: ARG001
            nonlocal in_flight, max_observed
            async with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
                if in_flight == parallelism:
                    all_started.set()
            await release.wait()
            async with lock:
                in_flight -= 1

        worker = MemoryStreamWorker(max_buffer_size=20, stop_timeout=2.0, max_parallel=parallelism)
        await worker.start(handler)
        for i in range(overflow):
            await worker.send(make_envelope(_Ping(tag=str(i))))
        with anyio.fail_after(5):
            await all_started.wait()
        # Overflow envelopes must stay queued, not running: max_parallel is an upper bound too.
        for _ in range(5):
            await checkpoint()
        assert max_observed == parallelism
        release.set()
        await worker.stop()


class TestMemoryStreamWorkerErrorIsolation:
    @staticmethod
    async def test_handler_exception_does_not_stop_worker(caplog: pytest.LogCaptureFixture) -> None:
        seen: list[str] = []
        processed = asyncio.Event()

        async def flaky_handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            if envelope.payload.tag == 'boom':
                msg = 'kaboom'
                raise RuntimeError(msg)
            seen.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=0.5)
        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.worker'):
            await worker.start(flaky_handler)
            await worker.send(make_envelope(_Ping(tag='boom')))
            await worker.send(make_envelope(_Ping(tag='ok')))
            await processed.wait()
            await worker.stop()

        assert seen == ['ok']
        assert 'Unhandled error' in caplog.text


class TestMemoryStreamWorkerPauseResume:
    @staticmethod
    async def test_pause_holds_dispatch_until_resume() -> None:
        received: list[str] = []
        processed = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            received.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=1.0)
        await worker.start(handler)
        await worker.pause()
        await worker.send(make_envelope(_Ping(tag='a')))
        # Give the worker real scheduling turns; while paused it must not dispatch.
        for _ in range(5):
            await checkpoint()
        assert received == []
        await worker.resume()
        with anyio.fail_after(5):
            await processed.wait()
        await worker.stop()

        assert received == ['a']
