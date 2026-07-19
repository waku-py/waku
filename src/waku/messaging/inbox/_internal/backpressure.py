from __future__ import annotations

import abc
import asyncio
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging._internal.pauser import PauseRegistry

if TYPE_CHECKING:
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.transport.interfaces import Subscription


class IListenerBackpressure(abc.ABC):
    """Minimal listener-gate seam: report the post-enqueue in-memory depth so the gate can pause/resume.

    Only ``observe_depth`` is shared (ISP) — the circuit breaker drives ``pause_listener``/``resume_listener`` on
    the concrete ``ListenerBackpressure`` directly — so a no-op sibling (``NoOpBackpressure``) can be the
    listener's default and the ``observe_depth`` call site never branches on the gate's absence.
    """

    __slots__ = ()

    @abc.abstractmethod
    async def observe_depth(self, depth: int) -> None: ...


class ListenerBackpressure(IListenerBackpressure):
    """One refcounted gate over a broker ``Subscription``, fed by two triggers that share a single resume.

    The circuit breaker drives ``pause_listener``/``resume_listener`` directly (their shape matches
    ``CircuitBreaker(pause=…, resume=…)``); the in-memory watermark drives ``observe_depth``. Each trigger mints/releases
    its own token, and the broker is stopped/resumed only on the gate's 0↔1 transitions — so neither trigger lifts the
    other's pause (the Wolverine ``Stopped``/``TooBusy`` invariant, reproduced by refcount).

    All gate transitions run under a single lock. ``Subscription.pause()``/``resume()`` are awaited broker round-trips,
    and ``observe_depth`` fires from two tasks (the enqueue path and the worker drain). Without serialization a
    drain-driven resume could land while an enqueue-driven pause is mid-flight and no-op against the subscription's
    running flag, stranding the listener stopped behind an open gate. The lock makes each transition atomic.

    Lock-ordering contract: nothing reached under ``_lock`` (only ``Subscription`` and ``PauseRegistry``) calls back
    into this object, so the lock is a leaf except for the circuit-breaker path, where ``CircuitBreaker._lock`` is held
    across ``pause_listener``/``resume_listener``. That single ``CircuitBreaker._lock -> _lock`` order never reverses,
    so the composition is deadlock-free — keep it that way if adding a second nested caller.
    """

    __slots__ = ('_gate', '_limits', '_lock', '_sub', '_wm_token')

    def __init__(self, *, subscription: Subscription, limits: BufferingLimits | None = None) -> None:
        self._sub = subscription
        self._limits = limits  # None => CB-only (no watermark); the gate still serves the CB
        self._gate = PauseRegistry()
        self._wm_token: PauseToken | None = None  # at most one watermark token
        self._lock = asyncio.Lock()

    async def pause_listener(self) -> PauseToken:
        async with self._lock:
            return await self._pause_gate()

    async def resume_listener(self, token: PauseToken) -> None:
        async with self._lock:
            await self._resume_gate(token)

    @override
    async def observe_depth(self, depth: int) -> None:
        if self._limits is None:
            return  # CB-only: no watermark configured
        async with self._lock:
            if self._wm_token is None and depth >= self._limits.high:
                self._wm_token = await self._pause_gate()
            elif self._wm_token is not None and depth <= self._limits.low:
                token, self._wm_token = self._wm_token, None
                await self._resume_gate(token)

    async def _pause_gate(self) -> PauseToken:
        was_paused = self._gate.paused
        token = self._gate.pause()
        if not was_paused:  # 0 -> 1: actually stop the broker listener
            await self._sub.pause()
        return token

    async def _resume_gate(self, token: PauseToken) -> None:
        self._gate.resume(token)
        if not self._gate.paused:  # 1 -> 0: resume broker delivery
            await self._sub.resume()
