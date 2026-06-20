from __future__ import annotations

from datetime import timedelta

import anyio.lowlevel

from waku.messaging.pauser import PauseRegistry, TimedPauser

from tests._wait import ControllableSleep, wait_until


async def test_two_timed_pausers_keep_gate_closed_until_both_release() -> None:
    registry = PauseRegistry()
    short_sleep, long_sleep = ControllableSleep(), ControllableSleep()
    short = TimedPauser(registry, sleep=short_sleep)
    long = TimedPauser(registry, sleep=long_sleep)

    await short.pause(timedelta(seconds=5))
    await long.pause(timedelta(seconds=600))
    await wait_until(lambda: short_sleep.requested == [5.0] and long_sleep.requested == [600.0])
    assert registry.paused is True

    short_sleep.released.set()
    # The gate must NOT open while the long token is still held.
    for _ in range(10):
        await anyio.lowlevel.checkpoint()
    assert registry.paused is True

    long_sleep.released.set()
    await wait_until(lambda: registry.paused is False)
    await short.aclose()
    await long.aclose()


def test_force_resume_opens_gate_with_token_still_held() -> None:
    registry = PauseRegistry()
    token = registry.pause()
    minted = registry.paused
    assert minted is True

    registry.force_resume()  # shutdown path: bypass refcount
    opened = registry.paused
    assert opened is False

    # Releasing a now-stale token after a force-resume must not re-close the gate.
    registry.resume(token)
    still_open = registry.paused
    assert still_open is False


async def test_aclose_cancels_pending_timed_release_so_gate_stays_closed() -> None:
    registry = PauseRegistry()
    sleep = ControllableSleep()
    pauser = TimedPauser(registry, sleep=sleep)
    await pauser.pause(timedelta(seconds=30))
    await wait_until(lambda: sleep.requested == [30.0])
    await pauser.aclose()  # cancel the parked release
    sleep.released.set()  # even if released now, the cancelled task must not release the token
    for _ in range(10):
        await anyio.lowlevel.checkpoint()
    assert registry.paused is True
