from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging._internal.dispatch import IEndpointDispatch  # noqa: TC001

# IEndpointDispatch + MessageRouter are DI-injected -> runtime imports (dishka
# introspects __init__ type hints at container-build time); the TC001 noqa keeps ruff from moving
# them under TYPE_CHECKING.
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.outgoing import Action, IOutgoingMessagesFrames
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messages import IMessage
    from waku.messaging.contracts.pipeline import CallNext
    from waku.messaging.endpoints.base import Endpoint
    from waku.messaging.outgoing import DeferredCascadeBatch, PendingMessage

__all__ = ['DeferredCascadeFlusher', 'DeferredCascadingBehavior', 'OutboxCascadingBehavior']

logger = logging.getLogger(__name__)


def _split_by_durability(
    router: MessageRouter,
    message_type: type[IMessage],
) -> tuple[list[Endpoint], list[Endpoint]]:
    # The router is an immutable singleton, so both behaviors re-partition to the IDENTICAL split;
    # is_outbox_backed is a fixed per-endpoint bool, so durable and non-durable are disjoint.
    endpoints = router.resolve(message_type)
    durable = [endpoint for endpoint in endpoints if endpoint.is_outbox_backed]
    non_durable = [endpoint for endpoint in endpoints if not endpoint.is_outbox_backed]
    return durable, non_durable


def _log_dropped_without_destination(pending: PendingMessage) -> None:
    # BC-27.1: a cascade resolving to zero destinations is dropped, never raised — a cascade is
    # fire-and-forget. Zero subscribers is normal for publish (fan-out parity with the bus); an
    # unrouted SEND is almost certainly a routing gap, so it gets a diagnostic WARNING.
    if pending.action is Action.SEND:
        logger.warning(
            'Cascaded send for %s resolved to zero destinations; dropping it',
            type(pending.message).__name__,
        )


class OutboxCascadingBehavior(IPipelineBehavior[Any, Any]):
    """Inner cascade behavior — dispatches each cascade per-destination by durability.

    Runs between ``TransactionalBehavior`` (outer) and the handler (inner). After the handler returns
    but BEFORE ``TransactionalBehavior`` commits, drains the current ``OutgoingMessages`` frame and
    splits each cascade's resolved destinations by durability:

    * outbox-backed destinations (``ExternalEndpoint``) -> dispatched NOW, to exactly those
      endpoints, so the outbox writes join the handler's session and commit atomically;
    * non-durable destinations (local queues) -> the cascade is staged into the deferred bucket via
      ``defer()``, re-partitioned and flushed post-commit by ``DeferredCascadingBehavior``.

    Each destination is served by exactly one leg: a mixed-durability type writes its durable leg
    in-tx and delivers its non-durable leg post-commit, never both to the same endpoint. A durable
    dispatch failure re-raises -> ``TransactionalBehavior`` rolls back both handler state and any
    partially written outbox rows: atomicity requires a durable cascade-write failure to fail the
    handler. A cascade resolving to ZERO destinations is dropped — silently for publish, with a
    WARNING for send (BC-27.1).
    """

    __slots__ = ('_dispatch', '_outgoing', '_router')

    def __init__(
        self,
        dispatch: IEndpointDispatch,
        outgoing: IOutgoingMessagesFrames,
        router: MessageRouter,
    ) -> None:
        self._dispatch = dispatch
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
            durable, non_durable = _split_by_durability(self._router, type(pending_message.message))
            if not durable and not non_durable:
                _log_dropped_without_destination(pending_message)
                continue
            if durable:
                await self._dispatch_in_tx(pending_message, durable)
            if non_durable:
                deferred.append(pending_message)
        if deferred:
            self._outgoing.defer(deferred)
        return response

    async def _dispatch_in_tx(self, pending: PendingMessage, endpoints: Sequence[Endpoint], /) -> None:
        try:
            await self._dispatch.dispatch_to(pending.message, endpoints)
        except Exception:
            logger.exception(
                'OutboxCascadingBehavior: durable cascade write failed for %s; '
                'transaction will be rolled back by TransactionalBehavior',
                type(pending.message).__name__,
            )
            raise


class DeferredCascadeFlusher:
    """Dispatch an explicitly detached cascade batch's non-durable legs.

    The transaction owner detaches the immutable batch before commit, exits the origin scope, and
    resolves this flusher from a fresh child. The flusher re-partitions each cascade with the same
    immutable-router split the inner behavior used and dispatches only the non-durable subset. A
    failure propagates to the owner, which classifies it as post-commit when appropriate.
    """

    __slots__ = ('_dispatch', '_router')

    def __init__(
        self,
        dispatch: IEndpointDispatch,
        router: MessageRouter,
    ) -> None:
        self._dispatch = dispatch
        self._router = router

    async def flush(self, batch: DeferredCascadeBatch, /) -> None:
        for pending_message in batch:
            _, non_durable = _split_by_durability(self._router, type(pending_message.message))
            await self._dispatch.dispatch_to(pending_message.message, non_durable)


class DeferredCascadingBehavior(IPipelineBehavior[Any, Any]):
    """Outer cascade behavior that owns the per-handler frame lifecycle.

    Runs OUTSIDE ``TransactionalBehavior`` (outermost global). Owns the frame lifecycle: pushes a
    frame before ``call_next()``, discards it on pipeline failure (a handler failure discards the
    staged non-durable batch), pops the (drained-by-the-inner-behavior, now-empty) frame on success.
    Cascade delivery is owned by the surrounding dispatcher or endpoint boundary after this behavior
    closes the frame; this behavior never flushes a detached batch itself.
    """

    __slots__ = ('_outgoing',)

    def __init__(
        self,
        outgoing: IOutgoingMessagesFrames,
    ) -> None:
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
        return response
