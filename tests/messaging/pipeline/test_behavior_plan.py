from __future__ import annotations

from typing import Any

from typing_extensions import override

from waku.messaging import RequestHandler
from waku.messaging._internal.outbox_cascading import DeferredCascadingBehavior, OutboxCascadingBehavior
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import MessagingConfig, OutboxConfig
from waku.messaging.modules import _FRAMEWORK_POLICIES
from waku.messaging.pipeline._internal.plan import build_behavior_plan

from tests.messaging.pipeline.conftest import Cmd, PassthroughBehavior, SomeBehavior


class _BehaviorA(PassthroughBehavior[Any, Any]): ...


class _BehaviorB(PassthroughBehavior[Any, Any]): ...


class _Handler(RequestHandler[Cmd, None]):
    behaviors = (SomeBehavior,)

    @override
    async def handle(self, request: Cmd, /) -> None: ...


class _Other(RequestHandler[Cmd, None]):
    @override
    async def handle(self, request: Cmd, /) -> None: ...


def test_plan_for_no_outbox_handler_matches_unified_cascade_chain() -> None:
    # After the cascade collapse the no-outbox plan is identical to the outbox plan: one cascade
    # subsystem (DeferredCascadingBehavior owns the frame, OutboxCascadingBehavior splits/defers)
    # attaches regardless of outbox presence.
    config = MessagingConfig(global_pipeline_behaviors=(TransactionalBehavior,))
    plan = build_behavior_plan([_Handler], _FRAMEWORK_POLICIES, config)
    assert plan.for_handler(_Handler) == (
        DeferredCascadingBehavior,
        TransactionalBehavior,
        OutboxCascadingBehavior,
        SomeBehavior,
    )


def test_plan_for_outbox_handler_matches_unified_cascade_chain() -> None:
    config = MessagingConfig(
        global_pipeline_behaviors=(TransactionalBehavior,),
        outbox=OutboxConfig(),
    )
    plan = build_behavior_plan([_Handler], _FRAMEWORK_POLICIES, config)
    assert plan.for_handler(_Handler) == (
        DeferredCascadingBehavior,
        TransactionalBehavior,
        OutboxCascadingBehavior,
        SomeBehavior,
    )


def test_plan_preserves_global_declaration_order() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(_BehaviorA, _BehaviorB))
    plan = build_behavior_plan([_Handler], _FRAMEWORK_POLICIES, config)
    chain = plan.for_handler(_Handler)
    assert chain.index(_BehaviorA) < chain.index(_BehaviorB)


def test_empty_plan_for_unknown_handler() -> None:
    config = MessagingConfig()
    plan = build_behavior_plan([_Handler], _FRAMEWORK_POLICIES, config)
    assert plan.for_handler(_Other) == ()
