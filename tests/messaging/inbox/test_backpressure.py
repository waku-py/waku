import anyio
import anyio.lowlevel
import pytest
from typing_extensions import override

from waku.messaging.inbox._internal.backpressure import ListenerBackpressure
from waku.messaging.inbox._internal.noop_backpressure import NoOpBackpressure
from waku.messaging.inbox.backpressure import BufferingLimits
from waku.messaging.transport.interfaces import Subscription


def test_buffering_limits_valid() -> None:
    limits = BufferingLimits(high=100, low=20)
    assert (limits.high, limits.low) == (100, 20)


def test_buffering_limits_low_not_below_high_raises() -> None:
    with pytest.raises(ValueError, match='low'):
        BufferingLimits(high=10, low=10)


def test_buffering_limits_negative_low_raises() -> None:
    with pytest.raises(ValueError, match='low'):
        BufferingLimits(high=10, low=-1)


class _RecordingSub(Subscription):
    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    async def pause(self) -> None:
        self.events.append('pause')

    @override
    async def resume(self) -> None:
        self.events.append('resume')


class _BlockingSub(Subscription):
    """Parks in ``pause()`` until released, so a concurrent ``resume()`` is forced to wait on the gate's lock."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.running = True
        self.pause_entered = anyio.Event()
        self.release_pause = anyio.Event()

    @override
    async def pause(self) -> None:
        self.pause_entered.set()
        await self.release_pause.wait()
        self.events.append('pause')
        self.running = False

    @override
    async def resume(self) -> None:
        self.events.append('resume')
        self.running = True


class TestListenerBackpressureWatermark:
    @staticmethod
    async def test_watermark_pauses_at_high_resumes_at_low() -> None:
        sub = _RecordingSub()
        bp = ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=10, low=2))

        await bp.observe_depth(10)
        await bp.observe_depth(11)  # already paused → no second pause
        await bp.observe_depth(2)

        assert sub.events == ['pause', 'resume']

    @staticmethod
    async def test_repeated_low_does_not_double_resume() -> None:
        sub = _RecordingSub()
        bp = ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=10, low=2))

        await bp.observe_depth(10)
        await bp.observe_depth(1)
        await bp.observe_depth(0)  # already resumed → no second resume

        assert sub.events == ['pause', 'resume']

    @staticmethod
    async def test_low_without_prior_high_is_noop() -> None:
        sub = _RecordingSub()
        bp = ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=10, low=2))

        await bp.observe_depth(1)

        assert sub.events == []

    @staticmethod
    async def test_no_limits_makes_observe_depth_a_noop() -> None:
        sub = _RecordingSub()
        bp = ListenerBackpressure(subscription=sub, limits=None)

        await bp.observe_depth(1_000)

        assert sub.events == []


class TestListenerBackpressureGate:
    @staticmethod
    async def test_cb_and_watermark_hold_one_gate_no_cross_resume() -> None:
        sub = _RecordingSub()
        bp = ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=10, low=2))

        cb_token = await bp.pause_listener()  # CB trips → mints its own token, stops the listener
        await bp.observe_depth(10)  # watermark crosses high but the gate is already paused → no second stop
        await bp.observe_depth(2)  # watermark releases its token, CB token still holds → stays paused
        assert sub.events == ['pause']

        await bp.resume_listener(cb_token)  # last token released → resume
        assert sub.events == ['pause', 'resume']

    @staticmethod
    async def test_concurrent_high_and_low_serialized_no_stuck_state() -> None:
        sub = _BlockingSub()
        bp = ListenerBackpressure(subscription=sub, limits=BufferingLimits(high=2, low=0))

        async with anyio.create_task_group() as tg:
            tg.start_soon(bp.observe_depth, 2)  # high → enters sub.pause(), parks holding the gate lock
            with anyio.fail_after(5):
                await sub.pause_entered.wait()
            tg.start_soon(bp.observe_depth, 0)  # low → must wait on the lock until pause completes
            for _ in range(5):
                await anyio.lowlevel.checkpoint()
            assert sub.events == []  # pause not yet recorded; resume cannot interleave ahead of it
            sub.release_pause.set()

        assert sub.events == ['pause', 'resume']  # serialized in order
        assert sub.running is True  # the listener is running again, not stranded stopped


async def test_noop_backpressure_observe_depth_never_touches_a_subscription() -> None:
    # The null gate the listener defaults to when no watermark/CB is wired: observe_depth is inert (no
    # subscription to touch, no raise) at any depth.
    await NoOpBackpressure().observe_depth(1_000)
