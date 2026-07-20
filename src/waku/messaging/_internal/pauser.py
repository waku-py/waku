from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta

__all__ = [
    'PauseRegistry',
    'PauseToken',
    'TimedPauser',
]


class PauseToken:
    """Opaque, identity-only handle for one hold on a PauseRegistry's gate."""

    __slots__ = ()


class PauseRegistry:
    """Refcounted pause gate. Composes N independent pausers over one asyncio.Event.

    Each ``pause()`` mints a token and clears the gate; ``resume(token)`` discards the token and opens
    the gate ONLY when no token remains. ``force_resume()`` opens the gate and drops every token,
    bypassing the refcount — shutdown must never strand the gate closed behind a leaked token.
    """

    __slots__ = ('_gate', '_tokens')

    def __init__(self) -> None:
        self._gate = asyncio.Event()
        self._gate.set()  # not paused by default
        self._tokens: set[PauseToken] = set()

    @property
    def paused(self) -> bool:
        return not self._gate.is_set()

    async def wait(self) -> None:
        await self._gate.wait()

    def pause(self) -> PauseToken:
        token = PauseToken()
        self._tokens.add(token)
        self._gate.clear()
        return token

    def resume(self, token: PauseToken) -> None:
        self._tokens.discard(token)
        if not self._tokens:
            self._gate.set()

    def force_resume(self) -> None:
        self._tokens.clear()
        self._gate.set()


class TimedPauser:
    """Holds a token for a fixed duration, then releases it. Shared by the PAUSE action."""

    __slots__ = ('_registry', '_sleep', '_tasks')

    def __init__(
        self,
        registry: PauseRegistry,
        *,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._registry = registry
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    async def pause(self, duration: timedelta) -> None:
        token = self._registry.pause()
        task = asyncio.create_task(self._run_release(token, duration.total_seconds()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_release(self, token: PauseToken, seconds: float) -> None:
        await self._sleep(seconds)
        self._registry.resume(token)

    async def aclose(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        for task in tuple(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
