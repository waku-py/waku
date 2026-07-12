from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.messaging import CallNext, IPipelineBehavior, IRequest, MessageT, RequestHandler, ResponseT
from waku.messaging._internal.registry import MessageRegistry
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import DeadLetterConfig, MessagingConfig, OutboxConfig
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.modules import _FRAMEWORK_POLICIES
from waku.messaging.pipeline._internal.plan import build_behavior_plan


@dataclass(frozen=True, slots=True)
class _Cmd(IRequest[None]):
    value: str


class _PassthroughBehavior(IPipelineBehavior[MessageT, ResponseT]):
    @override
    async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
        return await call_next()  # pragma: no cover -- plan tests never execute the chain


class _SomeBehavior(_PassthroughBehavior[Any, Any]): ...


class _PlainHandler(RequestHandler[_Cmd, None]):
    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _DeadLetterPolicyHandler(RequestHandler[_Cmd, None]):
    error_policies = (ErrorPolicy.on_any_exception().move_to_dead_letter(),)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerWithTransactionalLocal(RequestHandler[_Cmd, None]):
    behaviors = (TransactionalBehavior, _SomeBehavior)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


def _plan_for(handler: type[RequestHandler[_Cmd, None]], config: MessagingConfig) -> tuple[type[Any], ...]:
    plan = build_behavior_plan([handler], _FRAMEWORK_POLICIES, MessageRegistry(), config)
    return plan.for_handler(handler)


def test_outbox_handler_keeps_transactional() -> None:
    config = MessagingConfig(
        global_pipeline_behaviors=(TransactionalBehavior,),
        outbox=OutboxConfig(),
    )
    assert TransactionalBehavior in _plan_for(_PlainHandler, config)


def test_dead_letter_only_config_attaches_transactional_to_all_handlers() -> None:
    config = MessagingConfig(dead_letter=DeadLetterConfig())
    assert TransactionalBehavior in _plan_for(_PlainHandler, config)


def test_inbox_config_attaches_transactional_to_all_handlers() -> None:
    config = MessagingConfig(inbox=InboxConfig())
    assert TransactionalBehavior in _plan_for(_PlainHandler, config)


def test_local_transactional_under_durable_config_attaches_exactly_once() -> None:
    config = MessagingConfig(outbox=OutboxConfig())
    assert _plan_for(_HandlerWithTransactionalLocal, config).count(TransactionalBehavior) == 1


def test_global_transactional_attaches_to_all_handlers() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(TransactionalBehavior,))
    assert TransactionalBehavior in _plan_for(_PlainHandler, config)


def test_dead_letter_policy_handler_gets_transactional() -> None:
    config = MessagingConfig()
    assert TransactionalBehavior in _plan_for(_DeadLetterPolicyHandler, config)


def test_handler_local_transactional_is_single_and_at_user_global_tier() -> None:
    config = MessagingConfig()
    chain = _plan_for(_HandlerWithTransactionalLocal, config)
    assert chain.count(TransactionalBehavior) == 1
    assert chain.index(TransactionalBehavior) < chain.index(_SomeBehavior)


def test_plain_handler_without_uow_signal_omits_transactional() -> None:
    config = MessagingConfig()
    assert TransactionalBehavior not in _plan_for(_PlainHandler, config)
