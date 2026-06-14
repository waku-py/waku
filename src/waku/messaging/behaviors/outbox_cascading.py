from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.endpoints.external import ExternalEndpoint

# IMessageBus + MessageRouter are DI-injected -> runtime imports (dishka introspects __init__ type
# hints at container-build time); the TC001 noqa keeps ruff from moving them under TYPE_CHECKING.
from waku.messaging.interfaces import IMessageBus  # noqa: TC001
from waku.messaging.outgoing import Action, IOutgoingMessagesFrames
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.contracts.pipeline import CallNext
    from waku.messaging.outgoing import PendingMessage

__all__ = ['DeferredCascadingBehavior', 'OutboxCascadingBehavior']

logger = logging.getLogger(__name__)


class OutboxCascadingBehavior(IPipelineBehavior[Any, Any]):
    """Inner cascade behavior — partitions cascades by destination durability.

    Runs between ``TransactionalBehavior`` (outer) and the handler (inner). After the handler returns
    but BEFORE ``TransactionalBehavior`` commits, drains the current ``OutgoingMessages`` frame and
    resolves each cascade's destination via the router:

    * durable destination (``ExternalEndpoint``, outbox-backed) -> dispatch NOW, so the outbox write
      joins the handler's session and commits atomically;
    * non-durable destination (``LocalQueueEndpoint``, in-memory) -> stage into the deferred bucket via
      ``defer()``, to be flushed post-commit by ``DeferredCascadingBehavior``.

    A durable-dispatch failure re-raises -> ``TransactionalBehavior`` rolls back both handler state and
    any partially-written outbox rows: atomicity requires a durable cascade-write failure to fail the
    handler. Cascades with NO route resolve to an empty endpoint sequence; they are treated as
    non-durable (deferred) so the bus's own ``send``/``publish`` routing applies uniformly post-commit.
    """

    __slots__ = ('_bus', '_outgoing', '_router')

    def __init__(self, bus: IMessageBus, outgoing: IOutgoingMessagesFrames, router: MessageRouter) -> None:
        self._bus = bus
        self._outgoing = outgoing
        self._router = router

    @override
    async def handle(self, message: IMessage, /, call_next: CallNext[Any]) -> Any:
        response = await call_next()
        # drain (not pop) — DeferredCascadingBehavior's later pop_frame() must still find a frame
        # to pop at the outer pipeline level.
        pending = self._outgoing.drain_current_frame()
        deferred: list[PendingMessage] = []
        for pending_message in pending:
            if self._is_durable(pending_message):
                await self._dispatch_durable(pending_message)
            else:
                deferred.append(pending_message)
        if deferred:
            self._outgoing.defer(deferred)
        return response

    def _is_durable(self, pending: PendingMessage, /) -> bool:
        endpoints = self._router.resolve(type(pending.message))
        return any(isinstance(endpoint, ExternalEndpoint) for endpoint in endpoints)

    async def _dispatch_durable(self, pending: PendingMessage, /) -> None:
        try:
            if pending.action is Action.PUBLISH:
                await self._bus.publish(pending.message)
            else:
                await self._bus.send(pending.message)
        except Exception:
            logger.exception(
                'OutboxCascadingBehavior: durable cascade write failed for %s; '
                'transaction will be rolled back by TransactionalBehavior',
                type(pending.message).__name__,
            )
            raise


class DeferredCascadingBehavior(IPipelineBehavior[Any, Any]):
    """Outer cascade behavior — owns the frame and flushes the deferred bucket.

    Runs OUTSIDE ``TransactionalBehavior`` (outermost global), so its flush happens AFTER commit. Owns
    the frame lifecycle exactly like M2a.4's ``CascadingBehavior``: pushes a frame before
    ``call_next()``, discards it on pipeline failure, pops the (drained-by-the-inner-behavior, now-empty)
    frame on success. Then drains the deferred bucket — the non-durable cascades the inner
    ``OutboxCascadingBehavior`` staged — and dispatches each fire-and-forget. Failures are logged, not
    raised (the tx already committed); ``BaseException`` (cancellation) is NOT swallowed.
    """

    __slots__ = ('_bus', '_outgoing')

    def __init__(self, bus: IMessageBus, outgoing: IOutgoingMessagesFrames) -> None:
        self._bus = bus
        self._outgoing = outgoing

    @override
    async def handle(self, message: IMessage, /, call_next: CallNext[Any]) -> Any:
        self._outgoing.push_frame()
        try:
            response = await call_next()
        except BaseException:
            self._outgoing.discard_frame()
            raise
        self._outgoing.pop_frame()
        await self._flush_deferred()
        return response

    async def _flush_deferred(self) -> None:
        for pending_message in self._outgoing.drain_deferred():
            try:
                if pending_message.action is Action.PUBLISH:
                    await self._bus.publish(pending_message.message)
                else:
                    await self._bus.send(pending_message.message)
            except Exception:
                logger.exception(
                    'DeferredCascadingBehavior: cascade dispatch failed for %s',
                    type(pending_message.message).__name__,
                )
