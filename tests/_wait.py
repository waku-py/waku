from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.lowlevel

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['ControllableSleep', 'wait_until']


class ControllableSleep:
    """``sleep`` test double: records requested durations, blocks until ``released`` is set."""

    def __init__(self) -> None:
        self.released = anyio.Event()
        self.requested: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        await self.released.wait()


async def wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until *predicate* holds; fast-fails after 5s via ``anyio.fail_after``.

    Neutral home shared by the messaging and event-sourcing test trees.
    """
    with anyio.fail_after(5):
        while not predicate():
            await anyio.lowlevel.checkpoint()
