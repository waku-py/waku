import asyncio
from datetime import timedelta

import anyio
import pytest
from typing_extensions import override

from waku._internal.transaction import TransactionExecutionError, TransactionFailureKind
from waku.messaging import PollingConfig
from waku.messaging._internal.polling_agent import (
    AdaptivePace,
    FixedPace,
    Placement,
    PollingAgent,
)


def test_fixed_pace_returns_constant_delay_regardless_of_record() -> None:
    pace = FixedPace(seconds=2.5)
    assert pace.next_delay() == 2.5
    pace.record(0)
    assert pace.next_delay() == 2.5
    pace.record(7)
    assert pace.next_delay() == 2.5


def test_adaptive_pace_shrinks_to_min_after_work_and_grows_when_idle() -> None:
    config = PollingConfig(
        poll_interval_min_seconds=1.0,
        poll_interval_max_seconds=10.0,
        poll_interval_step_seconds=2.0,
        poll_interval_jitter_factor=0.0,
    )
    pace = AdaptivePace(config)
    pace.record(0)
    idle_first = pace.next_delay()
    pace.record(0)
    idle_second = pace.next_delay()
    assert idle_second > idle_first
    pace.record(5)
    assert pace.next_delay() == 1.0


class _FakeAgent(PollingAgent):
    placement = Placement.PER_POD

    def __init__(self) -> None:
        self.ticks = 0
        self.fail_once = False
        self.target = 3
        self.reached = anyio.Event()
        super().__init__(stop_timeout=timedelta(seconds=1))

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(seconds=0.01)

    @override
    async def _tick(self) -> int:
        self.ticks += 1
        if self.ticks >= self.target:
            self.reached.set()
        if self.fail_once:
            self.fail_once = False
            msg = 'boom'
            raise RuntimeError(msg)
        return 1


class _FailingAgent(PollingAgent):
    placement = Placement.PER_POD

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.tick_started = anyio.Event()
        super().__init__(stop_timeout=timedelta(seconds=1))

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(seconds=0.01)

    @override
    async def _tick(self) -> int:
        self.tick_started.set()
        raise self._error


async def test_polling_agent_runs_ticks_until_stopped() -> None:
    agent = _FakeAgent()
    await agent.start()
    with anyio.fail_after(5):
        await agent.reached.wait()
    await agent.stop()
    assert agent.ticks >= agent.target


async def test_polling_agent_tick_exception_does_not_break_loop() -> None:
    agent = _FakeAgent()
    agent.fail_once = True
    await agent.start()
    with anyio.fail_after(5):
        await agent.reached.wait()
    await agent.stop()
    assert agent.ticks >= agent.target


async def test_polling_agent_raw_cancellation_remains_cancellation() -> None:
    agent = _FailingAgent(asyncio.CancelledError())
    await agent.start()
    with anyio.fail_after(5):
        await agent.tick_started.wait()

    with pytest.raises(asyncio.CancelledError):
        await agent.stop()


async def test_polling_agent_direct_transaction_execution_error_preserves_identity() -> None:
    fatal = TransactionExecutionError(
        TransactionFailureKind.ROLLBACK_FAILED,
        RuntimeError('rollback failed'),
        RuntimeError('handler failed'),
    )
    agent = _FailingAgent(fatal)
    await agent.start()
    with anyio.fail_after(5):
        await agent.tick_started.wait()

    with pytest.raises(TransactionExecutionError) as raised:
        await agent.stop()

    assert raised.value is fatal


async def test_polling_agent_mixed_control_flow_group_remains_primary_during_stop() -> None:
    cancelled = asyncio.CancelledError()
    fatal = TransactionExecutionError(
        TransactionFailureKind.ROLLBACK_FAILED,
        RuntimeError('rollback failed'),
        RuntimeError('handler failed'),
    )
    agent = _FailingAgent(BaseExceptionGroup('mixed failure', [cancelled, fatal]))
    await agent.start()
    with anyio.fail_after(5):
        await agent.tick_started.wait()

    with pytest.raises(BaseExceptionGroup) as raised:
        await agent.stop()

    assert _exception_group_leaves(raised.value) == (cancelled, fatal)


async def test_polling_agent_fatal_group_unwrapping_preserves_identity_without_causal_chain() -> None:
    fatal = TransactionExecutionError(
        TransactionFailureKind.ROLLBACK_FAILED,
        RuntimeError('rollback failed'),
        RuntimeError('handler failed'),
    )
    agent = _FailingAgent(BaseExceptionGroup('fatal failure', [fatal]))
    await agent.start()
    with anyio.fail_after(5):
        await agent.tick_started.wait()

    with pytest.raises(TransactionExecutionError) as raised:
        await agent.stop()

    assert raised.value is fatal
    assert fatal.__cause__ is None
    assert fatal.__context__ is None


async def test_polling_agent_stop_is_idempotent_when_never_started() -> None:
    agent = _FakeAgent()
    await agent.stop()


async def test_second_start_while_running_raises() -> None:
    agent = _FakeAgent()
    await agent.start()
    with anyio.fail_after(5):
        await agent.reached.wait()
    with pytest.raises(RuntimeError, match='already started'):
        await agent.start()
    await agent.stop()


async def test_start_stop_start_polls_again() -> None:
    agent = _FakeAgent()
    await agent.start()
    with anyio.fail_after(5):
        await agent.reached.wait()
    await agent.stop()

    first_run_ticks = agent.ticks
    agent.reached = anyio.Event()
    agent.target = first_run_ticks + 3
    await agent.start()
    with anyio.fail_after(5):
        await agent.reached.wait()
    await agent.stop()

    assert agent.ticks >= first_run_ticks + 3


def _exception_group_leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for nested in error.exceptions for leaf in _exception_group_leaves(nested))
    return (error,)
