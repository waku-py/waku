from __future__ import annotations

from datetime import timedelta

import anyio.lowlevel

from waku.messaging.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from waku.messaging.endpoints.executor import ExecutionOutcome

from tests.messaging.helpers import wait_until


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _ControllableSleep:
    # Records requested sleeps; the test releases them explicitly (no real time elapses).
    def __init__(self) -> None:
        self.released = anyio.Event()
        self.requested: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        await self.released.wait()


def _make_breaker(
    config: CircuitBreakerConfig,
    *,
    clock: _Clock,
    sleep: _ControllableSleep,
) -> tuple[CircuitBreaker, list[str]]:
    calls: list[str] = []

    async def pause() -> None:  # noqa: RUF029 -- must be async to satisfy Callable[[], Awaitable[None]]
        calls.append('pause')

    async def resume() -> None:  # noqa: RUF029 -- must be async to satisfy Callable[[], Awaitable[None]]
        calls.append('resume')

    breaker = CircuitBreaker(config=config, pause=pause, resume=resume, now=clock, sleep=sleep)
    return breaker, calls


async def _record(breaker: CircuitBreaker, outcome: ExecutionOutcome, exc: Exception | None) -> None:
    await breaker.record(outcome, exc)


def _assert_state(breaker: CircuitBreaker, expected: CircuitState) -> None:
    assert breaker.state is expected


async def test_does_not_trip_below_minimum_throughput() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.2, minimum_throughput=10),
        clock=clock,
        sleep=sleep,
    )
    for _ in range(9):  # 9 < minimum_throughput; all failures
        await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.CLOSED)
    assert calls == []
    # the 10th failure reaches minimum_throughput → 10/10 = 1.0 > 0.2 → trips (pins the >= boundary)
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.OPEN)
    assert calls == ['pause']
    await breaker.aclose()  # cancel the parked resume task — clean teardown


async def test_trips_and_pauses_when_rate_exceeds_threshold() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=4),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.SUCCESS, None)
    await _record(breaker, ExecutionOutcome.SUCCESS, None)
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.CLOSED)  # 1/3 ≤ 0.5, and total 3 < 4
    await _record(breaker, ExecutionOutcome.DEAD_LETTERED, RuntimeError())  # 2/4 = 0.5, not > 0.5
    _assert_state(breaker, CircuitState.CLOSED)
    await _record(breaker, ExecutionOutcome.DEAD_LETTERED, RuntimeError())  # 3/5 = 0.6 > 0.5
    _assert_state(breaker, CircuitState.OPEN)
    assert calls == ['pause']
    await breaker.aclose()  # cancel the parked resume task — clean teardown


async def test_resumes_and_resets_after_pause_time() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=2, pause_time=timedelta(seconds=30)),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.OPEN)
    # The resume task is spawned via asyncio.create_task inside record() — it runs on the next loop
    # turn, so wait_until() (not a bare assert) is needed to observe it reach the controllable sleep.
    await wait_until(lambda: sleep.requested == [30.0])
    sleep.released.set()  # release the resume task
    await breaker.wait_for_resume()
    _assert_state(breaker, CircuitState.CLOSED)
    assert calls == ['pause', 'resume']
    # window was reset → a fresh failure does not immediately re-trip below minimum
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.CLOSED)


async def test_discarded_outcome_is_not_recorded() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.2, minimum_throughput=1),
        clock=clock,
        sleep=sleep,
    )
    for _ in range(20):
        await _record(breaker, ExecutionOutcome.DISCARDED, ValueError())
    _assert_state(breaker, CircuitState.CLOSED)
    assert calls == []


async def test_discarded_does_not_occupy_a_window_slot() -> None:
    # DISCARDED must not even enter the window (early-return). If it did — counted as a non-failure —
    # the window would hold 2 entries (1/2 = 0.5 > 0.4) and trip; with the early-return it holds only
    # the failure (total 1 < minimum 2) and stays CLOSED. Pins the window-slot effect, not just the rate.
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.4, minimum_throughput=2),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.DISCARDED, None)
    _assert_state(breaker, CircuitState.CLOSED)
    assert calls == []


async def test_only_tracked_exceptions_count_as_failures() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=2, track_exceptions=(TimeoutError,)),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, ValueError())  # untracked → non-failure
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, ValueError())  # 0/2 → CLOSED
    _assert_state(breaker, CircuitState.CLOSED)
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, TimeoutError())  # 1/3 = 0.33, not > 0.5
    _assert_state(breaker, CircuitState.CLOSED)
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, TimeoutError())  # 2/4 = 0.5, NOT > 0.5 (strict)
    _assert_state(breaker, CircuitState.CLOSED)
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, TimeoutError())  # 3/5 = 0.6 > 0.5 → trips
    _assert_state(breaker, CircuitState.OPEN)
    assert calls == ['pause']
    await breaker.aclose()  # cancel the parked resume task — clean teardown


async def test_ignored_exceptions_never_count_as_failures() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.2, minimum_throughput=2, ignore_exceptions=(ConnectionError,)),
        clock=clock,
        sleep=sleep,
    )
    for _ in range(10):  # all ignored → recorded as non-failures despite being terminal failures
        await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, ConnectionError())
    _assert_state(breaker, CircuitState.CLOSED)  # without the ignore check this would be 10/10 → tripped
    assert calls == []


async def test_old_entries_evicted_from_window() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=3, tracking_period=timedelta(seconds=10)),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    # 2 failures, total 2 < minimum_throughput=3 → no trip yet
    clock.t = 11.0  # both failures now older than the 10s window → evicted
    await _record(breaker, ExecutionOutcome.SUCCESS, None)
    # With eviction: window holds only the SUCCESS → 0/1, total 1 < min 3 → CLOSED.
    # Without eviction: 2 failures + 1 success → 2/3 > 0.5 and total 3 ≥ 3 → would trip. Pins _evict.
    _assert_state(breaker, CircuitState.CLOSED)
    assert calls == []


async def test_retrips_after_resume() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=2, pause_time=timedelta(seconds=30)),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.OPEN)
    await wait_until(lambda: sleep.requested == [30.0])
    sleep.released.set()
    await breaker.wait_for_resume()
    _assert_state(breaker, CircuitState.CLOSED)
    # a fresh failing burst after resume trips AGAIN (window reset + tripping re-armed)
    sleep.released = anyio.Event()  # re-arm the gate so the 2nd pause stays parked
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.OPEN)
    await wait_until(lambda: sleep.requested == [30.0, 30.0])
    assert calls == ['pause', 'resume', 'pause']
    await breaker.aclose()


async def test_aclose_cancels_pending_resume_so_resume_never_fires() -> None:
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=2, pause_time=timedelta(seconds=30)),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    await _record(breaker, ExecutionOutcome.FAILED_NO_POLICY, RuntimeError())
    _assert_state(breaker, CircuitState.OPEN)
    await wait_until(lambda: sleep.requested == [30.0])  # resume task parked in the sleep
    await breaker.aclose()  # cancels the parked task
    sleep.released.set()  # even if released now, the cancelled task must not run
    await anyio.lowlevel.checkpoint()
    assert calls == ['pause']  # resume() never fired — proves the cancel
    await breaker.aclose()  # idempotent — no-op on an already-closed breaker


async def test_dead_letter_failed_counts_as_failure() -> None:
    # ERR-2: a failed durable DLQ write is a processing failure — it must trip the breaker like
    # DEAD_LETTERED, not be treated as a success.
    clock, sleep = _Clock(), _ControllableSleep()
    breaker, calls = _make_breaker(
        CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=2),
        clock=clock,
        sleep=sleep,
    )
    await _record(breaker, ExecutionOutcome.DEAD_LETTER_FAILED, RuntimeError())
    await _record(breaker, ExecutionOutcome.DEAD_LETTER_FAILED, RuntimeError())  # 2/2 = 1.0 > 0.5 → trips
    _assert_state(breaker, CircuitState.OPEN)
    assert calls == ['pause']
    await breaker.aclose()
