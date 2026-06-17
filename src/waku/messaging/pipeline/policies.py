from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.behaviors.cascading import CascadingBehavior
from waku.messaging.behaviors.outbox_cascading import DeferredCascadingBehavior, OutboxCascadingBehavior
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.errors.policy import policies_need_dead_letter
from waku.messaging.pipeline.policy import IBehaviorPolicy, Position, PositionedBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.registry import MessageRegistry

__all__ = [
    'CascadingPolicy',
    'DeferredCascadingPolicy',
    'HandlerLocalPolicy',
    'OutboxDrainPolicy',
    'TransactionalPolicy',
    'UserGlobalPolicy',
]


def _config_requires_uow(config: MessagingConfig) -> bool:
    # Durable infra means inner outbox/inbox behaviors write inside the handler's transaction and need
    # its commit, so EVERY handler under such config gets the frame — even pure-read handlers that never
    # inject a UoW. A TransactionalBehavior listed globally is an explicit request for it everywhere.
    return (
        config.outbox is not None
        or config.inbox is not None
        or config.dead_letter is not None
        or any(issubclass(behavior, TransactionalBehavior) for behavior in config.global_pipeline_behaviors)
    )


def _handler_requires_uow(handler: HandlerType, config: MessagingConfig) -> bool:
    # Conservative superset of every handler that relies on the transactional frame: config-level need,
    # an explicit per-handler TransactionalBehavior, or a DEAD_LETTER policy whose row must persist
    # atomically. Bias is over-attach (a spurious frame is a harmless no-op commit; a missing one is a
    # silent atomicity loss).
    if _config_requires_uow(config):
        return True
    if any(issubclass(behavior, TransactionalBehavior) for behavior in handler.behaviors):
        return True
    return policies_need_dead_letter(handler.error_policies)


class CascadingPolicy(IBehaviorPolicy):
    """No-outbox path: CascadingBehavior outermost (post-commit). Attaches only when outbox is None."""

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if config.outbox is not None:
            return ()
        return (PositionedBehavior(CascadingBehavior, Position.CASCADE_FRAME),)


class DeferredCascadingPolicy(IBehaviorPolicy):
    """Outbox path: DeferredCascadingBehavior outermost (owns frame, post-commit flush)."""

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if config.outbox is None:
            return ()
        return (PositionedBehavior(DeferredCascadingBehavior, Position.CASCADE_FRAME),)


class OutboxDrainPolicy(IBehaviorPolicy):
    """Outbox path: OutboxCascadingBehavior innermost-global (inside TransactionalBehavior)."""

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if config.outbox is None:
            return ()
        return (PositionedBehavior(OutboxCascadingBehavior, Position.OUTBOX_DRAIN),)


class UserGlobalPolicy(IBehaviorPolicy):
    """Reproduces global_pipeline_behaviors at USER_GLOBAL, preserving declaration order via sequence.

    TransactionalBehavior is excluded: TransactionalPolicy is its sole, per-type owner so a handler that
    does not need a UoW gets no spurious transactional frame even when it is listed globally.
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        return tuple(
            PositionedBehavior(behavior, Position.USER_GLOBAL, sequence=index)
            for index, behavior in enumerate(config.global_pipeline_behaviors)
            if not issubclass(behavior, TransactionalBehavior)
        )


class TransactionalPolicy(IBehaviorPolicy):
    """Sole per-type owner of TransactionalBehavior placement.

    Attaches it once at USER_GLOBAL (between cascade and outbox-drain) for every handler that requires a
    UoW. sequence=-1 keeps it outermost of the user-global tier — exactly where a durability config lists
    it — so it wraps any other user globals (they run inside the transaction).
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if _handler_requires_uow(handler, config):
            return (PositionedBehavior(TransactionalBehavior, Position.USER_GLOBAL, sequence=-1),)
        return ()


class HandlerLocalPolicy(IBehaviorPolicy):
    """Per-handler `behaviors` ClassVar at HANDLER_LOCAL (inner of framework + user-global).

    TransactionalBehavior is excluded here too: TransactionalPolicy owns its placement (at USER_GLOBAL),
    so a handler listing it locally still gets a single frame, in the canonical outer position.
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        return tuple(
            PositionedBehavior(behavior, Position.HANDLER_LOCAL, sequence=index)
            for index, behavior in enumerate(handler.behaviors)
            if not issubclass(behavior, TransactionalBehavior)
        )
