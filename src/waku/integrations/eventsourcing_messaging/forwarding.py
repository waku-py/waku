from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

# Runtime imports: these are DI-injected into EventForwardingBehavior.__init__, which dishka
# introspects via get_type_hints at container-build time — they must resolve at runtime.
from waku.eventsourcing.forwarding import ForwardingRegistry, IAppendedEvents  # noqa: TC001
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.interfaces import ISender  # noqa: TC001
from waku.messaging.outgoing import IOutgoingMessages  # noqa: TC001
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.messaging.contracts.pipeline import CallNext

__all__ = [
    'EventForwardingBehavior',
]


class EventForwardingBehavior(IPipelineBehavior[Any, Any]):
    """Forwards event-store appends into the messaging outbox (Marten Event Forwarding parity).

    The PRODUCER half of M2e: it runs inner to ``OutboxCascadingBehavior``, so after the ES command
    handler appends, this behavior drains the scoped ``IAppendedEvents`` collector of ``StoredEvent``s
    and pushes each appended event (``stored.data`` raw, or a transform of the ``StoredEvent``) into
    ``IOutgoingMessages``. The outer ``OutboxCascadingBehavior`` is the sole
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
        for stored in self._appended.drain():
            await self._forward(stored)
        return result

    async def _forward(self, stored: StoredEvent, /) -> None:
        rule = self._registry.rule_for(type(stored.data))
        forwarded = rule.transform(stored) if rule.transform is not None else stored.data
        if rule.same_transaction:
            await self._sender.invoke(forwarded)
        elif self._router.resolve(type(forwarded)):
            self._outgoing.publish(forwarded)
