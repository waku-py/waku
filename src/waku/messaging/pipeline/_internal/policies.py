from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.outbox_cascading import DeferredCascadingBehavior, OutboxCascadingBehavior
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.errors.policy import policies_need_dead_letter
from waku.messaging.pipeline.policy import IBehaviorPolicy, Position, PositionedBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import IPipelineBehavior

__all__ = [
    'DeferredCascadingPolicy',
    'HandlerLocalPolicy',
    'OutboxDrainPolicy',
    'TransactionalPolicy',
    'UserGlobalPolicy',
]


def config_requires_uow(config: MessagingConfig) -> bool:
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
    if config_requires_uow(config):
        return True
    if any(issubclass(behavior, TransactionalBehavior) for behavior in handler.behaviors):
        return True
    return policies_need_dead_letter(handler.error_policies)


def _resolve_transactional_behavior(
    handler: HandlerType,
    config: MessagingConfig,
) -> type[IPipelineBehavior[Any, Any]]:
    # Honor a user-declared TransactionalBehavior subclass at the framework position. Candidates come from
    # BOTH the per-handler `behaviors` ClassVar and `global_pipeline_behaviors`; the MOST-DERIVED declared
    # class wins (the unique candidate that is a subclass of every other). With none declared the base frame
    # is installed (durable-config / dead-letter need). Two sibling subclasses have no unique most-derived
    # class — ambiguous config, rejected at startup.
    candidates = {
        behavior
        for behavior in (*handler.behaviors, *config.global_pipeline_behaviors)
        if issubclass(behavior, TransactionalBehavior)
    }
    if not candidates:
        return TransactionalBehavior
    most_derived = [c for c in candidates if all(issubclass(c, other) for other in candidates)]
    if len(most_derived) == 1:
        return most_derived[0]
    names = ', '.join(sorted(c.__name__ for c in candidates))
    msg = (
        f'handler {handler.__name__!r} resolves multiple sibling TransactionalBehavior subclasses '
        f'({names}) from its `behaviors` and `global_pipeline_behaviors`; no single most-derived class '
        'exists — declare exactly one, or make one subclass the other'
    )
    raise ImproperlyConfiguredError(msg)


class DeferredCascadingPolicy(IBehaviorPolicy):
    """DeferredCascadingBehavior outermost (owns the cascade frame, depth-aware post-commit flush).

    Attaches on every handler: with no outbox the durability split is empty and the flusher drains the
    fully-deferred bucket; with an outbox it flushes the non-durable legs after the durable ones
    committed in-tx. One cascade subsystem, no outbox-presence fork.
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        return (PositionedBehavior(DeferredCascadingBehavior, Position.CASCADE_FRAME),)


class OutboxDrainPolicy(IBehaviorPolicy):
    """OutboxCascadingBehavior innermost-global (inside TransactionalBehavior).

    Attaches on every handler: it splits each cascade's destinations by durability, dispatching the
    outbox-backed legs in-tx and deferring the rest. With no outbox every leg is non-durable, so it
    defers the whole batch to the post-commit flush — the same net delivery as the durable path.
    """

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
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
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        if _handler_requires_uow(handler, config):
            behavior = _resolve_transactional_behavior(handler, config)
            return (PositionedBehavior(behavior, Position.USER_GLOBAL, sequence=-1),)
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
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        return tuple(
            PositionedBehavior(behavior, Position.HANDLER_LOCAL, sequence=index)
            for index, behavior in enumerate(handler.behaviors)
            if not issubclass(behavior, TransactionalBehavior)
        )
