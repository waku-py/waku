from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import CallNext, IPipelineBehavior, IRequest, MessageT, RequestHandler, ResponseT
from waku.messaging._internal.outbox_cascading import DeferredCascadingBehavior, OutboxCascadingBehavior
from waku.messaging._internal.registry import MessageRegistry
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import MessagingConfig, OutboxConfig
from waku.messaging.modules import _FRAMEWORK_POLICIES
from waku.messaging.pipeline._internal.plan import build_behavior_plan

from tests.messaging.outbox.fake_store import FakeOutboxStore


@dataclass(frozen=True, slots=True)
class _Cmd(IRequest[None]):
    value: str


class _PassthroughBehavior(IPipelineBehavior[MessageT, ResponseT]):
    @override
    async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
        return await call_next()  # pragma: no cover -- plan tests never execute the chain


class _SomeBehavior(_PassthroughBehavior[Any, Any]): ...


class _AuditTxn(TransactionalBehavior): ...


class _MetricsTxn(TransactionalBehavior): ...


class _DeepTxn(_AuditTxn): ...


class _PlainHandler(RequestHandler[_Cmd, None]):
    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerDeclaresSubclass(RequestHandler[_Cmd, None]):
    behaviors = (_AuditTxn, _SomeBehavior)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerDeclaresSubclassOnly(RequestHandler[_Cmd, None]):
    behaviors = (_AuditTxn,)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerDeclaresBase(RequestHandler[_Cmd, None]):
    behaviors = (TransactionalBehavior, _SomeBehavior)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerDeclaresParentAndGrandchild(RequestHandler[_Cmd, None]):
    behaviors = (_AuditTxn, _DeepTxn)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


class _HandlerDeclaresSiblings(RequestHandler[_Cmd, None]):
    behaviors = (_AuditTxn, _MetricsTxn)

    @override
    async def handle(self, request: _Cmd, /) -> None: ...


def _plan_for(handler: type[RequestHandler[_Cmd, None]], config: MessagingConfig) -> tuple[type[Any], ...]:
    plan = build_behavior_plan([handler], _FRAMEWORK_POLICIES, MessageRegistry(), config)
    return plan.for_handler(handler)


# --- Task 1: honor a declared subclass ---


def test_handler_declared_subclass_installed_once_at_framework_position() -> None:
    chain = _plan_for(_HandlerDeclaresSubclass, MessagingConfig())
    assert chain.count(_AuditTxn) == 1
    assert TransactionalBehavior not in chain
    assert chain.index(_AuditTxn) < chain.index(_SomeBehavior)


def test_global_declared_subclass_installed_once() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(_AuditTxn,))
    chain = _plan_for(_PlainHandler, config)
    assert chain.count(_AuditTxn) == 1
    assert TransactionalBehavior not in chain


def test_plain_base_declared_stays_base() -> None:
    chain = _plan_for(_HandlerDeclaresBase, MessagingConfig())
    assert chain.count(TransactionalBehavior) == 1
    assert _AuditTxn not in chain
    assert chain.index(TransactionalBehavior) < chain.index(_SomeBehavior)


def test_grandchild_wins_over_parent_and_base() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(TransactionalBehavior,))
    chain = _plan_for(_HandlerDeclaresParentAndGrandchild, config)
    assert chain.count(_DeepTxn) == 1
    assert _AuditTxn not in chain
    assert TransactionalBehavior not in chain


# --- Task 2: durable-config interaction + independence ---


def test_subclass_declared_under_outbox_config_installs_subclass_not_base() -> None:
    config = MessagingConfig(outbox=OutboxConfig(store=FakeOutboxStore))
    chain = _plan_for(_HandlerDeclaresSubclassOnly, config)
    assert chain[:3] == (DeferredCascadingBehavior, _AuditTxn, OutboxCascadingBehavior)
    assert TransactionalBehavior not in chain


def test_bare_handler_under_outbox_config_installs_base() -> None:
    config = MessagingConfig(outbox=OutboxConfig(store=FakeOutboxStore))
    chain = _plan_for(_PlainHandler, config)
    assert chain.count(TransactionalBehavior) == 1
    assert _AuditTxn not in chain


def test_two_handlers_subclass_and_plain_independent() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(TransactionalBehavior,))
    plan = build_behavior_plan(
        [_HandlerDeclaresSubclassOnly, _PlainHandler],
        _FRAMEWORK_POLICIES,
        MessageRegistry(),
        config,
    )
    sub_chain = plan.for_handler(_HandlerDeclaresSubclassOnly)
    plain_chain = plan.for_handler(_PlainHandler)
    assert _AuditTxn in sub_chain
    assert TransactionalBehavior not in sub_chain
    assert TransactionalBehavior in plain_chain
    assert _AuditTxn not in plain_chain


def test_plain_handler_no_uow_signal_still_omits_frame() -> None:
    chain = _plan_for(_PlainHandler, MessagingConfig())
    assert TransactionalBehavior not in chain
    assert _AuditTxn not in chain


# --- Task 3: reject sibling subclasses (named invariant breaker) ---


def test_sibling_subclasses_raise_improperly_configured() -> None:
    config = MessagingConfig()
    with pytest.raises(ImproperlyConfiguredError, match=r'_AuditTxn.*_MetricsTxn.*declare exactly one'):
        build_behavior_plan([_HandlerDeclaresSiblings], _FRAMEWORK_POLICIES, MessageRegistry(), config)


def test_sibling_split_across_sources_raises() -> None:
    config = MessagingConfig(global_pipeline_behaviors=(_MetricsTxn,))
    with pytest.raises(ImproperlyConfiguredError, match=r'_AuditTxn.*_MetricsTxn.*declare exactly one'):
        build_behavior_plan([_HandlerDeclaresSubclassOnly], _FRAMEWORK_POLICIES, MessageRegistry(), config)
