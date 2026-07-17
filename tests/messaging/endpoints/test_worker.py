from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import anyio
import pytest
from anyio.lowlevel import checkpoint

from waku._internal.transaction import AfterCommitError, RollbackFailedError, TransactionExecutionError
from waku.messages import IMessage
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker

from tests.messaging.helpers import make_envelope


@dataclass(frozen=True, slots=True)
class _Ping(IMessage):
    tag: str


class TestMemoryStreamWorkerLifecycle:
    @staticmethod
    async def test_send_before_start_returns_false() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))

        accepted = await worker.send(make_envelope(_Ping(tag='a')))

        assert accepted is False

    @staticmethod
    async def test_start_then_send_routes_envelope_to_handler() -> None:
        received: list[str] = []
        processed = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            received.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))
        await worker.start(handler)
        accepted = await worker.send(make_envelope(_Ping(tag='a')))
        await processed.wait()
        await worker.stop()

        assert accepted is True
        assert received == ['a']

    @staticmethod
    async def test_stop_without_start_is_noop() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))

        await worker.stop()

    @staticmethod
    async def test_send_after_stop_returns_false() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))
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

        worker = MemoryStreamWorker(max_buffer_size=10, stop_timeout=timedelta(seconds=1.0), max_parallel=1)
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

        worker = MemoryStreamWorker(max_buffer_size=20, stop_timeout=timedelta(seconds=2.0), max_parallel=parallelism)
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


class TestMemoryStreamWorkerDepth:
    @staticmethod
    async def test_queue_depth_zero_before_start() -> None:
        worker: MemoryStreamWorker[int] = MemoryStreamWorker()

        assert worker.queue_depth == 0

    @staticmethod
    async def test_on_drain_fires_with_depth_after_dequeue() -> None:
        seen: list[int] = []
        processed = asyncio.Event()

        async def on_drain(depth: int) -> None:  # noqa: RUF029
            seen.append(depth)

        async def handler(_item: int) -> None:  # noqa: RUF029
            processed.set()

        worker: MemoryStreamWorker[int] = MemoryStreamWorker(
            max_buffer_size=4, stop_timeout=timedelta(seconds=1.0), max_parallel=1
        )
        await worker.start(handler, on_drain=on_drain)
        await worker.send(1)
        with anyio.fail_after(5):
            await processed.wait()
        await worker.stop()

        assert seen == [0]  # the single item is dequeued before the handler runs, so the buffer is empty on drain


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

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))
        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints._internal.worker'):
            await worker.start(flaky_handler)
            await worker.send(make_envelope(_Ping(tag='boom')))
            await worker.send(make_envelope(_Ping(tag='ok')))
            await processed.wait()
            await worker.stop()

        assert seen == ['ok']
        assert 'Unhandled error' in caplog.text

    @staticmethod
    async def test_mixed_control_flow_group_remains_primary_during_stop() -> None:
        cancelled = asyncio.CancelledError()
        fatal = RollbackFailedError(
            RuntimeError('rollback failed'),
            RuntimeError('handler failed'),
        )
        failure = BaseExceptionGroup('mixed failure', [cancelled, fatal])
        handled = asyncio.Event()

        async def handler(_item: int) -> None:  # noqa: RUF029
            handled.set()
            raise failure

        worker: MemoryStreamWorker[int] = MemoryStreamWorker(max_buffer_size=1, stop_timeout=timedelta(seconds=0.5))
        await worker.start(handler)
        await worker.send(1)
        await handled.wait()

        with pytest.raises(BaseExceptionGroup) as raised:
            await worker.stop()

        assert list(_exception_group_leaves(raised.value)) == [cancelled, fatal]

    @staticmethod
    async def test_fatal_group_unwrapping_preserves_identity_without_causal_chain() -> None:
        fatal = RollbackFailedError(
            RuntimeError('rollback failed'),
            RuntimeError('handler failed'),
        )
        failure = BaseExceptionGroup('fatal failure', [fatal])
        handled = asyncio.Event()

        async def handler(_item: int) -> None:  # noqa: RUF029
            handled.set()
            raise failure

        worker: MemoryStreamWorker[int] = MemoryStreamWorker(max_buffer_size=1, stop_timeout=timedelta(seconds=0.5))
        await worker.start(handler)
        await worker.send(1)
        await handled.wait()

        with pytest.raises(TransactionExecutionError) as raised:
            await worker.stop()

        assert raised.value is fatal
        assert fatal.__cause__ is None
        assert fatal.__context__ is None

    @staticmethod
    async def test_failing_on_drain_does_not_mask_post_commit_failure() -> None:
        teardown_error = RuntimeError('request scope teardown failed')
        await _assert_fatal_signal_survives_failing_on_drain(
            AfterCommitError(teardown_error),
        )

    @staticmethod
    async def test_failing_on_drain_does_not_mask_rollback_failure() -> None:
        rollback_error = RuntimeError('rollback failed')
        await _assert_fatal_signal_survives_failing_on_drain(
            RollbackFailedError(
                rollback_error,
                RuntimeError('handler failed'),
            ),
        )


def _exception_group_leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for nested in error.exceptions for leaf in _exception_group_leaves(nested))
    return (error,)


async def _assert_fatal_signal_survives_failing_on_drain(signal: TransactionExecutionError) -> None:
    async def handler(_item: int) -> None:  # noqa: RUF029
        raise signal

    async def on_drain(_depth: int) -> None:  # noqa: RUF029
        msg = 'drain hook failed'
        raise RuntimeError(msg)

    worker: MemoryStreamWorker[int] = MemoryStreamWorker(
        max_buffer_size=1,
        stop_timeout=timedelta(seconds=0.5),
    )
    await worker.start(handler, on_drain=on_drain)
    await worker.send(1)

    with pytest.raises(TransactionExecutionError) as raised:
        await worker.stop()

    assert raised.value is signal


class TestMemoryStreamWorkerPauseResume:
    @staticmethod
    async def test_pause_holds_dispatch_until_resume() -> None:
        received: list[str] = []
        processed = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            received.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=1.0))
        await worker.start(handler)
        token = await worker.pause()
        await worker.send(make_envelope(_Ping(tag='a')))
        # Give the worker real scheduling turns; while paused it must not dispatch.
        for _ in range(5):
            await checkpoint()
        assert received == []
        await worker.resume(token)
        with anyio.fail_after(5):
            await processed.wait()
        await worker.stop()

        assert received == ['a']


class TestMemoryStreamWorkerPauseTokens:
    @staticmethod
    async def test_two_pause_tokens_block_until_both_resumed() -> None:
        received: list[str] = []
        processed = asyncio.Event()

        async def handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: RUF029
            received.append(envelope.payload.tag)
            processed.set()

        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=1.0))
        await worker.start(handler)
        token_a = await worker.pause()
        token_b = await worker.pause()
        await worker.send(make_envelope(_Ping(tag='a')))
        await worker.resume(token_a)  # one hold released; the other still pauses
        for _ in range(10):
            await checkpoint()
        assert received == []
        await worker.resume(token_b)
        with anyio.fail_after(5):
            await processed.wait()
        await worker.stop()
        assert received == ['a']

    @staticmethod
    async def test_stop_force_resumes_with_token_still_held() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=1.0))
        await worker.start(_noop)
        await worker.pause()  # token intentionally leaked
        await worker.stop()  # must not hang waiting on a paused consumer


class TestMemoryStreamWorkerTrySend:
    @staticmethod
    async def test_try_send_returns_false_before_start() -> None:
        worker = MemoryStreamWorker(max_buffer_size=4, stop_timeout=timedelta(seconds=0.5))

        assert worker.try_send(make_envelope(_Ping(tag='a'))) is False

    @staticmethod
    async def test_try_send_rejects_full_buffer_without_blocking() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_handler(envelope: MessageEnvelope[Any]) -> None:  # noqa: ARG001
            started.set()
            await release.wait()

        worker = MemoryStreamWorker(max_buffer_size=1, stop_timeout=timedelta(seconds=1.0), max_parallel=1)
        await worker.start(blocking_handler)
        assert await worker.send(make_envelope(_Ping(tag='held'))) is True
        with anyio.fail_after(5):
            await started.wait()  # the worker pulled 'held' and is parked in the handler -> buffer empty
        assert worker.try_send(make_envelope(_Ping(tag='buffered'))) is True  # fills the single slot
        assert worker.try_send(make_envelope(_Ping(tag='overflow'))) is False  # full -> WouldBlock -> False
        release.set()
        await worker.stop()
