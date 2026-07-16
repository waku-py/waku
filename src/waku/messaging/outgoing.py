from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messages import IEvent, IMessage
    from waku.messaging.contracts.request import IRequest

__all__ = [
    'IOutgoingMessages',
]


class Action(enum.Enum):
    SEND = 'send'
    PUBLISH = 'publish'


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingMessage:
    message: IMessage
    action: Action


DeferredCascadeBatch: TypeAlias = tuple[PendingMessage, ...]


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

    Consumed by the cascade family: ``DeferredCascadingBehavior`` (outer) owns the
    frame (push/pop/discard around pipeline dispatch); ``OutboxCascadingBehavior`` (inner) calls
    ``drain_current_frame``, dispatches each cascade's outbox-backed destinations
    pre-commit, and calls ``defer`` for cascades that also (or only) resolve to
    non-durable destinations. NOT exported from ``waku.messaging`` — handler authors
    must not depend on this.
    """

    def push_frame(self) -> None: ...
    def pop_frame(self) -> list[PendingMessage]: ...
    def discard_frame(self) -> None: ...
    def drain_current_frame(self) -> list[PendingMessage]: ...
    def defer(self, messages: Sequence[PendingMessage], /) -> None: ...
    def detach_deferred(self) -> DeferredCascadeBatch: ...

    @property
    def pending(self) -> Sequence[PendingMessage]: ...


class OutgoingMessages(IOutgoingMessages, IOutgoingMessagesFrames):
    """Frame-stack collector implementing both Protocols.

    Registered via ``scoped(AnyOf[IOutgoingMessages, IOutgoingMessagesFrames],
    OutgoingMessages)`` — dishka provides the SAME instance under both Protocol
    types within a request scope, while the concrete class stays
    framework-internal (not directly injectable).
    """

    __slots__ = ('_deferred', '_frames')

    def __init__(self) -> None:
        self._frames: list[list[PendingMessage]] = []
        self._deferred: list[PendingMessage] = []

    @override
    def send(self, request: IRequest[Any], /) -> None:
        self._current_frame.append(PendingMessage(message=request, action=Action.SEND))

    @override
    def publish(self, event: IEvent, /) -> None:
        self._current_frame.append(PendingMessage(message=event, action=Action.PUBLISH))

    @override
    def push_frame(self) -> None:
        """Start a new nesting level. Called by ``DeferredCascadingBehavior`` before dispatch."""
        self._frames.append([])

    @override
    def pop_frame(self) -> list[PendingMessage]:
        """Complete current level, return its messages. Called by ``DeferredCascadingBehavior`` after success.

        Paired with ``push_frame`` by ``DeferredCascadingBehavior`` (push on entry, pop on success /
        discard on failure); an unpaired ``pop_frame``/``discard_frame`` raises ``IndexError``.
        """
        return self._frames.pop()

    @override
    def discard_frame(self) -> None:
        """Discard current level's messages. Called by ``DeferredCascadingBehavior`` on pipeline failure."""
        self._frames.pop()

    @override
    def drain_current_frame(self) -> list[PendingMessage]:
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

    @override
    def defer(self, messages: Sequence[PendingMessage], /) -> None:
        """Stage cascades with non-durable destinations for post-commit flush.

        Called by ``OutboxCascadingBehavior`` (inner/in-tx) for each cascade whose
        resolved destinations include at least one non-durable endpoint. The
        deferred bucket is a flat list (not a frame stack): it is drained ONCE
        post-commit by ``DeferredCascadingBehavior`` (outer), which re-partitions
        and dispatches ONLY the non-durable subset. Durable and non-durable
        destination subsets are disjoint per message, so no ENDPOINT is served
        twice — a mixed-durability cascade legitimately appears both in the outbox
        (durable leg) and here (non-durable leg).
        """
        self._deferred.extend(messages)

    @override
    def detach_deferred(self) -> DeferredCascadeBatch:
        """Detach an immutable FIFO batch and clear the scoped deferred bucket."""
        detached = tuple(self._deferred)
        self._deferred.clear()
        return detached

    @property
    @override
    def pending(self) -> Sequence[PendingMessage]:
        """Read-only snapshot of the current frame (empty if no frame is active)."""
        if not self._frames:
            return ()
        return tuple(self._current_frame)

    @property
    def _current_frame(self) -> list[PendingMessage]:
        if not self._frames:
            msg = 'IOutgoingMessages.send/publish called with no active cascade frame (use it only inside a message handler)'
            raise RuntimeError(msg)
        return self._frames[-1]
