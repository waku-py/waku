from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.eventsourcing.decider.handler import DeciderCommandHandler
from waku.eventsourcing.forwarding import EventForwardingBehavior
from waku.eventsourcing.handler import EventSourcedCommandHandler
from waku.messaging.pipeline.policy import IBehaviorPolicy, Position, PositionedBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.registry import MessageRegistry

__all__ = ['ForwardingPolicy']

_ES_COMMAND_HANDLER_BASES = (EventSourcedCommandHandler, DeciderCommandHandler)


class ForwardingPolicy(IBehaviorPolicy):
    """Attaches EventForwardingBehavior (innermost) to every ES command handler.

    Contributed by the ES module across the module boundary, so messaging stays ignorant of event sourcing.
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if issubclass(handler, _ES_COMMAND_HANDLER_BASES):
            return (PositionedBehavior(EventForwardingBehavior, Position.FORWARDING),)
        return ()
