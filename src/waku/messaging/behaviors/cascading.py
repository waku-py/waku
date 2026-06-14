from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging.contracts.pipeline import IPipelineBehavior

# Runtime imports: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so DI-injected types must resolve at runtime — not under TYPE_CHECKING.
from waku.messaging.interfaces import IMessageBus  # noqa: TC001
from waku.messaging.outgoing import Action, IOutgoingMessagesFrames

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.contracts.message import IMessage
    from waku.messaging.contracts.pipeline import CallNext
    from waku.messaging.outgoing import PendingMessage

__all__ = ['CascadingBehavior']

logger = logging.getLogger(__name__)


class CascadingBehavior(IPipelineBehavior[Any, Any]):
    """Collects handler-emitted cascades and flushes them after the handler.

    Runs in the SAME scope as the handler (it is a pipeline behavior; the pipeline
    runs in the bus scope for ``invoke`` and in the worker child scope for
    ``publish``/``send``). So the ``IOutgoingMessagesFrames`` it resolves IS the same
    scoped instance the handler injects as ``IOutgoingMessages`` — no scope mismatch.

    Position: OUTSIDE ``TransactionalBehavior`` (framework places it outermost, at
    index 0 of the resolved chain) → flush runs AFTER commit. Design B: post-commit and
    isolated. Each cascade is re-dispatched via the bus (``publish``/``send``) and routed
    independently; delivery timing follows the destination endpoint (deferred on a background
    worker for buffered/durable endpoints, synchronous for inline). A cascade failure is
    isolated — it neither rolls back nor surfaces to the originating handler.

    This is the NO-OUTBOX path: with no outbox configured, no endpoint can be durable,
    so every cascade flushes post-commit here. When an outbox IS configured, cascade
    handling becomes per-destination via M2b.1's two position-keyed behaviors
    (``OutboxCascadingBehavior`` inner/in-tx + ``DeferredCascadingBehavior`` outer/post-commit).
    """

    __slots__ = ('_bus', '_outgoing')

    def __init__(self, outgoing: IOutgoingMessagesFrames, bus: IMessageBus) -> None:
        self._outgoing = outgoing
        self._bus = bus

    @override
    async def handle(self, message: IMessage, /, call_next: CallNext[Any]) -> Any:
        self._outgoing.push_frame()
        try:
            response = await call_next()
        except BaseException:
            self._outgoing.discard_frame()
            raise
        pending = self._outgoing.pop_frame()
        await self._flush(pending)
        return response

    async def _flush(self, pending: Sequence[PendingMessage]) -> None:
        """Re-dispatch each cascade via the bus. Failures logged, not raised.

        The handler already succeeded (and committed, if transactional), so a cascade
        dispatch failure — including ``NoRouteError`` on a cascaded ``send`` — must not
        surface to the originating caller. ``BaseException`` (cancellation) is NOT swallowed.
        """
        for pending_message in pending:
            try:
                if pending_message.action is Action.PUBLISH:
                    await self._bus.publish(pending_message.message)
                else:
                    await self._bus.send(pending_message.message)
            except Exception:
                logger.exception(
                    'Failed to dispatch cascading message %s',
                    type(pending_message.message).__name__,
                )
