import anyio
from typing_extensions import override

from waku.messaging import PollingConfig
from waku.messaging._polling_agent import (  # noqa: PLC2701
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
        super().__init__(stop_timeout=1.0)

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


async def test_polling_agent_stop_is_idempotent_when_never_started() -> None:
    agent = _FakeAgent()
    await agent.stop()
