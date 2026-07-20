import asyncio
import logging
import math
from collections.abc import Callable
from datetime import timedelta

import anyio
import pytest
from typing_extensions import override

from waku import ImproperlyConfiguredError
from waku._internal.transaction import AfterCommitError, RollbackFailedError, TransactionExecutionError
from waku.messaging import PollingConfig
from waku.messaging._internal.polling_agent import (
    AdaptivePace,
    FixedPace,
    Placement,
    PollingAgent,
)

from tests._wait import wait_until


@pytest.mark.parametrize('value', [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_polling_config_rejects_invalid_minimum(value: float) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='poll_interval_min_seconds must be finite and > 0'):
        PollingConfig(poll_interval_min_seconds=value)


@pytest.mark.parametrize('value', [math.inf, -math.inf, math.nan])
def test_polling_config_rejects_non_finite_maximum(value: float) -> None:
    with pytest.raises(
        ImproperlyConfiguredError,
        match='poll_interval_max_seconds must be finite and >= poll_interval_min_seconds',
    ):
        PollingConfig(poll_interval_max_seconds=value)


def test_polling_config_rejects_reversed_bounds() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='poll_interval_max_seconds'):
        PollingConfig(poll_interval_min_seconds=0.5, poll_interval_max_seconds=0.49)


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_polling_config_rejects_invalid_step(value: float) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='poll_interval_step_seconds must be finite and >= 0'):
        PollingConfig(poll_interval_step_seconds=value)


@pytest.mark.parametrize('value', [-0.1, 1.0, 1.1, math.inf, -math.inf, math.nan])
def test_polling_config_rejects_invalid_jitter(value: float) -> None:
    with pytest.raises(
        ImproperlyConfiguredError,
        match=r'poll_interval_jitter_factor must be finite and in \[0, 1\)',
    ):
        PollingConfig(poll_interval_jitter_factor=value)


def test_polling_config_rejects_jittered_minimum_underflow() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='jittered minimum'):
        PollingConfig(
            poll_interval_min_seconds=math.ulp(0.0),
            poll_interval_max_seconds=1.0,
            poll_interval_jitter_factor=0.5,
        )


def test_polling_config_rejects_jittered_maximum_overflow() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='jittered maximum'):
        PollingConfig(
            poll_interval_max_seconds=math.nextafter(math.inf, 0.0),
            poll_interval_jitter_factor=0.5,
        )


def test_polling_config_accepts_fixed_pace_boundaries() -> None:
    config = PollingConfig(
        poll_interval_min_seconds=0.5,
        poll_interval_max_seconds=0.5,
        poll_interval_step_seconds=0.0,
        poll_interval_jitter_factor=0.0,
    )

    pace = AdaptivePace(config)
    pace.record(0)

    assert pace.next_delay() == 0.5


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


class _RetryingAgent(PollingAgent):
    """Subclass that opts out of the terminal-on-fatal default, as the membership heartbeat does."""

    placement = Placement.PER_POD
    retries_after_fatal = True

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.ticks = 0
        super().__init__(stop_timeout=timedelta(seconds=1))

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(seconds=0.01)

    @override
    async def _tick(self) -> int:
        self.ticks += 1
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
    fatal = RollbackFailedError(
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


async def test_polling_agent_logs_critical_when_poll_loop_dies_with_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fatal = RollbackFailedError(
        RuntimeError('rollback failed'),
        RuntimeError('handler failed'),
    )
    agent = _FailingAgent(fatal)
    with caplog.at_level(logging.CRITICAL, logger='waku.messaging._internal.polling_agent'):
        await agent.start()
        # The fatal death is visible in flight, before any shutdown-time stop() retrieves it.
        await wait_until(lambda: 'terminated with an unrecovered fatal error' in caplog.text)
        with pytest.raises(TransactionExecutionError):
            await agent.stop()

    assert 'terminated with an unrecovered fatal error' in caplog.text


async def test_polling_agent_cancellation_death_is_not_logged_as_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _FailingAgent(asyncio.CancelledError())
    with caplog.at_level(logging.CRITICAL, logger='waku.messaging._internal.polling_agent'):
        await agent.start()
        with anyio.fail_after(5):
            await agent.tick_started.wait()
        with pytest.raises(asyncio.CancelledError):
            await agent.stop()

    assert 'terminated with an unrecovered fatal error' not in caplog.text


async def test_polling_agent_mixed_control_flow_group_remains_primary_during_stop() -> None:
    cancelled = asyncio.CancelledError()
    fatal = RollbackFailedError(
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
    fatal = RollbackFailedError(
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


def _rollback_failure_fatal() -> TransactionExecutionError:
    return RollbackFailedError(RuntimeError('rollback failed'), RuntimeError('handler failed'))


def _after_commit_failure_fatal() -> TransactionExecutionError:
    return AfterCommitError(RuntimeError('after commit failed'))


def _after_commit_cancellation_fatal() -> TransactionExecutionError:
    return AfterCommitError(asyncio.CancelledError())


@pytest.mark.parametrize(
    'wrap',
    [
        pytest.param(False, id='bare_fatal'),
        pytest.param(True, id='group_wrapped_fatal'),
    ],
)
@pytest.mark.parametrize(
    'make_fatal',
    [
        pytest.param(_rollback_failure_fatal, id='rollback_failure'),
        pytest.param(_after_commit_failure_fatal, id='after_commit_failure'),
    ],
)
async def test_polling_agent_retrying_subclass_keeps_looping_after_a_fatal(
    make_fatal: Callable[[], TransactionExecutionError],
    wrap: bool,
) -> None:
    fatal = make_fatal()
    error: BaseException = BaseExceptionGroup('fatal failure', [fatal]) if wrap else fatal
    agent = _RetryingAgent(error)

    await agent.start()
    await wait_until(lambda: agent.ticks >= 3)
    await agent.stop()

    assert agent.ticks >= 3


@pytest.mark.parametrize(
    'wrap',
    [
        pytest.param(False, id='bare_fatal_wrapping_cancellation'),
        pytest.param(True, id='deferrable_group_around_fatal_wrapping_cancellation'),
    ],
)
async def test_polling_agent_retrying_subclass_stops_when_the_fatal_wraps_cancellation(wrap: bool) -> None:
    # The exact shape a committed transaction produces when cancellation lands during child-scope
    # teardown: the control flow rides *inside* the fatal's payload rather than beside it in a group.
    fatal = _after_commit_cancellation_fatal()
    error: BaseException = BaseExceptionGroup('fatal failure', [fatal]) if wrap else fatal
    agent = _RetryingAgent(error)

    await agent.start()
    await wait_until(lambda: agent.ticks >= 1)

    with pytest.raises(TransactionExecutionError) as raised:
        await agent.stop()

    assert raised.value is fatal
    assert agent.ticks == 1


async def test_polling_agent_retrying_subclass_still_propagates_a_bare_cancellation() -> None:
    agent = _RetryingAgent(asyncio.CancelledError())

    await agent.start()
    await wait_until(lambda: agent.ticks >= 1)

    with pytest.raises(asyncio.CancelledError):
        await agent.stop()

    assert agent.ticks == 1


@pytest.mark.parametrize(
    'wrap',
    [
        pytest.param(False, id='bare_fatal_wrapping_cancellation'),
        pytest.param(True, id='deferrable_group_around_fatal_wrapping_cancellation'),
    ],
)
async def test_polling_agent_default_ends_the_loop_when_the_fatal_wraps_cancellation(wrap: bool) -> None:
    fatal = _after_commit_cancellation_fatal()
    error: BaseException = BaseExceptionGroup('fatal failure', [fatal]) if wrap else fatal
    agent = _FailingAgent(error)

    await agent.start()
    with anyio.fail_after(5):
        await agent.tick_started.wait()

    with pytest.raises(TransactionExecutionError) as raised:
        await agent.stop()

    assert raised.value is fatal


async def test_polling_agent_retrying_subclass_still_propagates_a_cancellation_carrying_group() -> None:
    # The retry policy covers store failure, never control flow: a fatal that surfaced alongside a
    # cancellation must still end the loop, or the override would silently demote cancellation.
    cancelled = asyncio.CancelledError()
    fatal = RollbackFailedError(RuntimeError('rollback failed'), RuntimeError('handler failed'))
    agent = _RetryingAgent(BaseExceptionGroup('mixed failure', [cancelled, fatal]))

    await agent.start()
    await wait_until(lambda: agent.ticks >= 1)

    with pytest.raises(BaseExceptionGroup) as raised:
        await agent.stop()

    assert _exception_group_leaves(raised.value) == (cancelled, fatal)


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
