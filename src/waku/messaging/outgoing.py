from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.contracts.request import IRequest

__all__ = [
    'IOutgoingMessages',
    'OutgoingMessages',
]


class _Action(enum.Enum):
    SEND = 'send'
    PUBLISH = 'publish'


@dataclass(frozen=True, slots=True, kw_only=True)
class _PendingMessage:
    message: IMessage
    action: _Action


class IOutgoingMessages(Protocol):
    """Handler-facing collector for cascading messages.

    Handlers inject this narrow Protocol and use it ONLY to schedule cascades
    for dispatch after the pipeline finishes successfully.
    """

    def send(self, request: IRequest[Any], /) -> None:
        """Schedule a request for fire-and-forget dispatch after handler success."""
        ...

    def publish(self, event: IEvent, /) -> None:
        """Schedule an event for fan-out dispatch after handler success."""
        ...


class IOutgoingMessagesFrames(Protocol):
    """Framework-internal frame lifecycle + deferred-bucket API.

    Consumed by ``CascadingBehavior`` (push/pop/discard around pipeline dispatch,
    no-outbox path) and by the per-destination outbox family (M2b.1):
    ``OutboxCascadingBehavior`` (inner) calls ``drain_current_frame`` to flush
    durable-destined cascades into the outbox pre-commit and ``defer`` to stage
    non-durable-destined ones; ``DeferredCascadingBehavior`` (outer) calls
    ``drain_deferred`` to flush the staged non-durable cascades post-commit. NOT
    exported from ``waku.messaging`` — handler authors must not depend on this.
    """

    def push_frame(self) -> None: ...
    def pop_frame(self) -> list[_PendingMessage]: ...
    def discard_frame(self) -> None: ...
    def drain_current_frame(self) -> list[_PendingMessage]: ...
    def defer(self, messages: Sequence[_PendingMessage], /) -> None: ...
    def drain_deferred(self) -> list[_PendingMessage]: ...

    @property
    def pending(self) -> Sequence[_PendingMessage]: ...


class OutgoingMessages(IOutgoingMessages, IOutgoingMessagesFrames):
    """Frame-stack collector implementing both Protocols.

    Registered via ``scoped(AnyOf[IOutgoingMessages, IOutgoingMessagesFrames],
    OutgoingMessages)`` — dishka provides the SAME instance under both Protocol
    types within a request scope, while the concrete class stays
    framework-internal (not directly injectable).
    """

    __slots__ = ('_deferred', '_frames')

    def __init__(self) -> None:
        self._frames: list[list[_PendingMessage]] = []
        self._deferred: list[_PendingMessage] = []

    def send(self, request: IRequest[Any], /) -> None:
        self._current_frame.append(_PendingMessage(message=request, action=_Action.SEND))

    def publish(self, event: IEvent, /) -> None:
        self._current_frame.append(_PendingMessage(message=event, action=_Action.PUBLISH))

    def push_frame(self) -> None:
        """Start a new nesting level. Called by ``CascadingBehavior`` before dispatch."""
        self._frames.append([])

    def pop_frame(self) -> list[_PendingMessage]:
        """Complete current level, return its messages. Called by ``CascadingBehavior`` after pipeline success.

        Paired with ``push_frame`` by ``CascadingBehavior`` (push on entry, pop on success /
        discard on failure); an unpaired ``pop_frame``/``discard_frame`` raises ``IndexError``.
        """
        return self._frames.pop()

    def discard_frame(self) -> None:
        """Discard current level's messages. Called by ``CascadingBehavior`` on pipeline failure."""
        self._frames.pop()

    def drain_current_frame(self) -> list[_PendingMessage]:
        """Return + clear contents of the current frame WITHOUT popping it.

        Used by ``OutboxCascadingBehavior`` (M2b.1) to flush cascades inside the
        handler's transaction. The bus's later ``pop_frame()`` still finds an
        empty frame to pop — frame-stack depth is preserved.
        """
        if not self._frames:
            return []
        current = self._current_frame
        drained = list(current)
        current.clear()
        return drained

    def defer(self, messages: Sequence[_PendingMessage], /) -> None:
        """Stage non-durable-destined cascades for post-commit flush.

        Called by ``OutboxCascadingBehavior`` (M2b.1, inner/in-tx) for cascades
        whose destination endpoint is NOT durable. The deferred bucket is a flat
        list (not a frame stack): it is drained ONCE post-commit by
        ``DeferredCascadingBehavior`` (outer), so no per-level nesting is needed.
        Disjoint from the outbox writes the inner behavior performs for durable
        destinations — guaranteeing no message is both written and deferred.
        """
        self._deferred.extend(messages)

    def drain_deferred(self) -> list[_PendingMessage]:
        """Return + clear the deferred bucket. Called by ``DeferredCascadingBehavior``."""
        drained = list(self._deferred)
        self._deferred.clear()
        return drained

    @property
    def pending(self) -> Sequence[_PendingMessage]:
        """Read-only snapshot of the current frame (empty if no frame is active)."""
        if not self._frames:
            return ()
        return tuple(self._current_frame)

    @property
    def _current_frame(self) -> list[_PendingMessage]:
        if not self._frames:
            msg = 'IOutgoingMessages.send/publish called with no active cascade frame (use it only inside a message handler)'
            raise RuntimeError(msg)
        return self._frames[-1]
