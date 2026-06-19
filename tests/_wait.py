from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.lowlevel

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['wait_until']


async def wait_until(predicate: Callable[[], bool]) -> None:
    """Yield to the event loop until *predicate* holds (or a 5s fast-fail deadline trips).

    Deterministic alternative to ``anyio.sleep`` for awaiting background-worker effects: re-checks
    on each scheduler turn, fast-fails via ``anyio.fail_after``. Neutral home (shared by the messaging
    and event-sourcing test trees) so neither tree imports across the other.
    """
    with anyio.fail_after(5):
        while not predicate():
            await anyio.lowlevel.checkpoint()
