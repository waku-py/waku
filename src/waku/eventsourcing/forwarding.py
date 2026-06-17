from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from typing_extensions import override

from waku.messaging.contracts.pipeline import IPipelineBehavior

# Runtime imports: these are DI-injected into EventForwardingBehavior.__init__, which dishka
# introspects via get_type_hints at container-build time — they must resolve at runtime.
from waku.messaging.interfaces import ISender  # noqa: TC001
from waku.messaging.outgoing import IOutgoingMessages  # noqa: TC001
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.pipeline import CallNext

__all__ = [
    'AppendedEventsCollector',
    'EventForwardingBehavior',
    'ForwardDescriptor',
    'ForwardingRegistry',
    'IAppendedEvents',
    'forward',
]


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


class EventForwardingBehavior(IPipelineBehavior[Any, Any]):
    """Forwards event-store appends into the messaging outbox (Marten Event Forwarding parity).

    The PRODUCER half of M2e: it runs inner to ``OutboxCascadingBehavior``, so after the ES command
    handler appends, this behavior drains the scoped ``IAppendedEvents`` collector and pushes each
    appended event into ``IOutgoingMessages``. The outer ``OutboxCascadingBehavior`` is the sole
    CONSUMER that drains the frame into the outbox in the handler's transaction — single drain, no
    double-flush. This behavior never writes the outbox itself.

    Per the two-axes model, the default forward is ``publish`` (deferred, isolated, post-commit,
    subscriber-gated: unrouted events are silently dropped). An event with a ``same_transaction``
    rule is instead forwarded via ``invoke`` (inline, same transaction, fail-fast: raises
    ``HandlerNotFound`` if no handler is registered).

    Forwarding only fires for event stores that record appended events into ``IAppendedEvents`` — i.e.
    ``SqlAlchemyEventStore``. A store that does not record (e.g. ``InMemoryEventStore``) forwards
    nothing; pair forwarding with a recording store.
    """

    __slots__ = ('_appended', '_outgoing', '_registry', '_router', '_sender')

    def __init__(
        self,
        appended: IAppendedEvents,
        outgoing: IOutgoingMessages,
        router: MessageRouter,
        registry: ForwardingRegistry,
        sender: ISender,
    ) -> None:
        self._appended = appended
        self._outgoing = outgoing
        self._router = router
        self._registry = registry
        self._sender = sender

    @override
    async def handle(self, message: Any, /, call_next: CallNext[Any]) -> Any:
        result = await call_next()
        for event in self._appended.drain():
            await self._forward(event)
        return result

    async def _forward(self, event: IEvent, /) -> None:
        rule = self._registry.rule_for(type(event))
        forwarded = rule.transform(event) if rule.transform is not None else event
        if rule.same_transaction:
            await self._sender.invoke(forwarded)
        elif self._router.resolve(type(forwarded)):
            self._outgoing.publish(forwarded)
