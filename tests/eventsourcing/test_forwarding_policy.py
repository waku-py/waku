from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.eventsourcing.forwarding import EventForwardingBehavior
from waku.eventsourcing.forwarding_policy import ForwardingPolicy
from waku.eventsourcing.handler import EventSourcedVoidCommandHandler
from waku.messaging import IRequest, RequestHandler
from waku.messaging.behaviors.outbox_cascading import OutboxCascadingBehavior
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import MessagingConfig, OutboxConfig
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.modules import _FRAMEWORK_POLICIES  # noqa: PLC2701
from waku.messaging.pipeline.policy import build_behavior_plan
from waku.messaging.registry import MessageRegistry

from tests.messaging.helpers import RecordingTransport
from tests.messaging.outbox.fake_store import FakeOutboxStore

_POLICIES = (*_FRAMEWORK_POLICIES, ForwardingPolicy())


@dataclass(frozen=True, kw_only=True)
class _DoThing(IRequest[None]):
    aggregate_id: str


class _ESHandler(EventSourcedVoidCommandHandler[_DoThing, Any]):
    @override
    def _aggregate_id(self, request: _DoThing) -> str:
        return request.aggregate_id

    @override
    async def _execute(self, request: _DoThing, aggregate: Any) -> None: ...


class _PlainHandler(RequestHandler[_DoThing, None]):
    @override
    async def handle(self, request: _DoThing, /) -> None: ...


def _chain_for(handler: HandlerType, config: MessagingConfig) -> tuple[type[Any], ...]:
    return build_behavior_plan([handler], _POLICIES, MessageRegistry(), config).for_handler(handler)


def test_es_command_handler_gets_forwarding_innermost() -> None:
    config = MessagingConfig(
        global_pipeline_behaviors=(TransactionalBehavior,),
        outbox=OutboxConfig(store=FakeOutboxStore, transport=RecordingTransport),
    )
    chain = _chain_for(_ESHandler, config)
    assert chain[-1] is EventForwardingBehavior
    assert chain.index(OutboxCascadingBehavior) < chain.index(EventForwardingBehavior)


def test_non_es_handler_gets_no_forwarding() -> None:
    chain = _chain_for(_PlainHandler, MessagingConfig())
    assert EventForwardingBehavior not in chain


def test_broken_subclass_no_longer_raises_at_class_def_and_still_forwards() -> None:
    class _OverridesBehaviors(_ESHandler):
        behaviors = ()

    chain = _chain_for(_OverridesBehaviors, MessagingConfig())
    assert EventForwardingBehavior in chain
