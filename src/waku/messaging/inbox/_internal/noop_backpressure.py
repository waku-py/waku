from __future__ import annotations

from typing_extensions import override

from waku.messaging.inbox.backpressure import IListenerBackpressure


class NoOpBackpressure(IListenerBackpressure):
    """Null listener gate: the default when no watermark or inbound circuit breaker is configured.

    ``observe_depth`` is inert, so the listener's depth report is always safe to make unconditionally — the
    construction cycle (listener -> subscription -> gate) forbids a real gate as the ``__init__`` default, and
    this no-arg null object is the only default that lets the listener drop its absence guard type-safely.
    """

    __slots__ = ()

    @override
    async def observe_depth(self, depth: int) -> None: ...
