from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.messages import IEvent

__all__ = [
    'AppendedEventsCollector',
    'ForwardDescriptor',
    'ForwardingConsumer',
    'ForwardingRegistry',
    'IAppendedEvents',
    'forward',
]


@final
class ForwardingConsumer:
    """Presence token: a consumer that drains appended events into the message bus is installed.

    ES core produces appended events but never consumes them — the ES<->messaging bridge does. The
    bridge registers this token so ES-side startup validation fails loud when ``forwarding=[...]`` is
    configured but no consumer is wired (otherwise forwarding silently no-ops).
    """

    __slots__ = ()


class IAppendedEvents(Protocol):
    """Scoped hand-off of the domain events appended during a command's transaction.

    The event store calls ``record()`` on the real-append path and ``clear()`` on each
    ``append_to_stream`` entry (per optimistic-retry attempt); ``EventForwardingBehavior`` calls
    ``drain()`` after the handler returns. This is an Event-Sourcing-local contract holding raw
    ``IEvent``s — no bus, no router — so the store stays ignorant of messaging.
    """

    def clear(self) -> None: ...

    def record(self, events: Sequence[IEvent], /) -> None: ...

    def drain(self) -> list[IEvent]: ...


class AppendedEventsCollector(IAppendedEvents):
    """In-memory ``scoped`` collector: one per DI command scope, spanning all retry attempts.

    The scoped lifetime alone is too coarse — a single command scope spans every optimistic-retry
    attempt — so the store ``clear()``s on each ``append_to_stream`` entry. The result: after the
    winning attempt returns, the collector holds exactly the events that survived to commit.
    """

    __slots__ = ('_events',)

    def __init__(self) -> None:
        self._events: list[IEvent] = []

    def clear(self) -> None:
        self._events.clear()

    def record(self, events: Sequence[IEvent], /) -> None:
        self._events.extend(events)

    def drain(self) -> list[IEvent]:
        drained = list(self._events)
        self._events.clear()
        return drained


@dataclass(frozen=True, slots=True)
class _ForwardRule:
    transform: Callable[[IEvent], IEvent] | None = None
    same_transaction: bool = False


_DEFAULT_RULE = _ForwardRule()


@dataclass(frozen=True, slots=True)
class ForwardDescriptor:
    """A per-event-type forwarding rule, produced by ``forward(...)``."""

    event_type: type[IEvent]
    rule: _ForwardRule


class ForwardBuilder:
    """Fluent builder for an opt-in forwarding rule (Marten ``SubscribeToEvent`` analog)."""

    __slots__ = ('_event_type',)

    def __init__(self, event_type: type[IEvent]) -> None:
        self._event_type = event_type

    def transformed_to(
        self,
        transform: Callable[[IEvent], IEvent],
        /,
        *,
        same_transaction: bool = False,
    ) -> ForwardDescriptor:
        """Map the internal event to an integration event before forwarding."""
        return ForwardDescriptor(self._event_type, _ForwardRule(transform=transform, same_transaction=same_transaction))

    def same_transaction(self) -> ForwardDescriptor:
        """Forward the raw event inline in the command's transaction via ``invoke`` (not the outbox)."""
        return ForwardDescriptor(self._event_type, _ForwardRule(same_transaction=True))


def forward(event_type: type[IEvent]) -> ForwardBuilder:
    """Begin an opt-in forwarding rule for an appended event type.

    Without a rule, an appended event is forwarded raw, post-commit, subscriber-gated (forwarded only
    when a route exists). Use ``forward(EventType).transformed_to(fn)`` to map internal -> integration
    events, and ``.same_transaction()`` to handle it inline in the command's transaction instead.

    Contract: the order of forwarded events is NOT guaranteed; and a forwarded event should not ALSO be
    emitted as a handler cascade of the same type (forward XOR cascade), to avoid duplicate delivery.
    """
    return ForwardBuilder(event_type)


class ForwardingRegistry:
    """Immutable lookup of per-event-type forwarding rules, built once from config."""

    __slots__ = ('_rules',)

    def __init__(self, descriptors: Sequence[ForwardDescriptor] = ()) -> None:
        self._rules = {descriptor.event_type: descriptor.rule for descriptor in descriptors}

    def rule_for(self, event_type: type[IEvent]) -> _ForwardRule:
        return self._rules.get(event_type, _DEFAULT_RULE)
