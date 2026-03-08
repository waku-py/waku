from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from waku.eventsourcing._retry import execute_with_optimistic_retry  # noqa: PLC2701
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError

from tests.eventsourcing.helpers import RecordingContext


async def test_attempt_context_entered_and_exited_on_success() -> None:
    ctx = RecordingContext()
    result = await execute_with_optimistic_retry(
        AsyncMock(return_value=42),
        max_attempts=3,
        request_name='Test',
        aggregate_id='a-1',
        attempt_context=lambda: ctx,
    )
    assert result == 42
    assert ctx.entered == 1
    assert ctx.exited == 1
    assert ctx.exit_exceptions == [None]


async def test_attempt_context_rolled_back_on_conflict_then_fresh_on_retry() -> None:
    contexts: list[RecordingContext] = []

    def make_context() -> RecordingContext:
        c = RecordingContext()
        contexts.append(c)
        return c

    calls = 0
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('T', 'a-1'),
        expected_version=0,
        actual_version=1,
    )

    async def attempt() -> str:  # noqa: RUF029
        nonlocal calls
        calls += 1
        if calls == 1:
            raise conflict
        return 'ok'

    result = await execute_with_optimistic_retry(
        attempt,
        max_attempts=3,
        request_name='Test',
        aggregate_id='a-1',
        attempt_context=make_context,
    )

    assert result == 'ok'
    assert len(contexts) == 2
    assert contexts[0].exit_exceptions == [ConcurrencyConflictError]
    assert contexts[1].exit_exceptions == [None]


async def test_attempt_context_rolled_back_on_non_concurrency_error() -> None:
    ctx = RecordingContext()

    async def attempt() -> str:  # noqa: RUF029
        msg = 'boom'
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match='boom'):
        await execute_with_optimistic_retry(
            attempt,
            max_attempts=3,
            request_name='Test',
            aggregate_id='a-1',
            attempt_context=lambda: ctx,
        )

    assert ctx.entered == 1
    assert ctx.exited == 1
    assert ctx.exit_exceptions == [RuntimeError]


async def test_nullcontext_as_attempt_context() -> None:
    result = await execute_with_optimistic_retry(
        AsyncMock(return_value=99),
        max_attempts=1,
        request_name='Test',
        aggregate_id='a-1',
        attempt_context=nullcontext,
    )
    assert result == 99


async def test_all_retries_exhausted_raises_conflict() -> None:
    contexts: list[RecordingContext] = []

    def make_context() -> RecordingContext:
        c = RecordingContext()
        contexts.append(c)
        return c

    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('T', 'a-1'),
        expected_version=0,
        actual_version=1,
    )

    async def attempt() -> str:  # noqa: RUF029
        raise conflict

    max_attempts = 3

    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await execute_with_optimistic_retry(
            attempt,
            max_attempts=max_attempts,
            request_name='Test',
            aggregate_id='a-1',
            attempt_context=make_context,
        )

    assert exc_info.value is conflict
    assert len(contexts) == max_attempts
    for ctx in contexts:
        assert ctx.entered == 1
        assert ctx.exited == 1
        assert ctx.exit_exceptions == [ConcurrencyConflictError]
