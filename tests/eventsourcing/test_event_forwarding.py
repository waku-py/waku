from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest
from typing_extensions import override

from waku.eventsourcing.forwarding import (
    AppendedEventsCollector,
    EventForwardingBehavior,
    ForwardingRegistry,
    forward,
)
from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.interfaces import ISender
from waku.messaging.outgoing import IOutgoingMessages
from waku.messaging.router import MessageRouter

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.endpoints.base import Endpoint


@dataclass(frozen=True)
class RoutedEvent(IEvent):
    note: str = ''


@dataclass(frozen=True)
class UnroutedEvent(IEvent):
    note: str = ''


@dataclass(frozen=True)
class IntegrationEvent(IEvent):
    note: str = ''


class _RecordingOutgoing(IOutgoingMessages):
    def __init__(self) -> None:
        self.published: list[IEvent] = []

    @override
    def send(self, request: IRequest[Any], /) -> None:  # pragma: no cover
        msg = 'forwarding must not send'
        raise AssertionError(msg)

    @override
    def publish(self, event: IEvent, /) -> None:
        self.published.append(event)


class _RecordingSender(ISender):
    def __init__(self) -> None:
        self.invoked: list[IMessage] = []

    @override
    async def invoke(self, message: Any, /) -> Any:
        self.invoked.append(message)
        return None

    @override
    async def send(self, message: IMessage, /) -> None:  # pragma: no cover
        msg = 'forwarding must not send'
        raise AssertionError(msg)


def _router_for(*routed_types: type[IMessage]) -> MessageRouter:
    endpoint = cast('Endpoint', object())
    return MessageRouter(routes=dict.fromkeys(routed_types, (endpoint,)), endpoints=())


def _behavior(
    collector: AppendedEventsCollector,
    outgoing: _RecordingOutgoing,
    router: MessageRouter,
    sender: _RecordingSender,
    registry: ForwardingRegistry | None = None,
) -> EventForwardingBehavior:
    return EventForwardingBehavior(collector, outgoing, router, registry or ForwardingRegistry(), sender)


async def test_routed_appended_event_forwarded_raw_to_outgoing() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender)
    event = RoutedEvent('e1')

    async def call_next() -> str:  # noqa: RUF029
        collector.record([event])
        return 'response'

    result = await behavior.handle(object(), call_next)

    assert result == 'response'
    assert outgoing.published == [event]
    assert outgoing.published[0] is event  # raw by default
    assert sender.invoked == []


async def test_unrouted_appended_event_is_dropped() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    behavior = _behavior(collector, outgoing, _router_for(), sender)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([UnroutedEvent('e1')])

    await behavior.handle(object(), call_next)

    assert outgoing.published == []
    assert sender.invoked == []


async def test_registered_transform_forwards_integration_event() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    registry = ForwardingRegistry([
        forward(RoutedEvent).transformed_to(lambda e: IntegrationEvent(cast('RoutedEvent', e).note))
    ])
    behavior = _behavior(collector, outgoing, _router_for(IntegrationEvent), sender, registry)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([RoutedEvent('payload')])

    await behavior.handle(object(), call_next)

    assert outgoing.published == [IntegrationEvent('payload')]
    assert sender.invoked == []


async def test_same_transaction_rule_invokes_inline_not_outbox() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    registry = ForwardingRegistry([forward(RoutedEvent).same_transaction()])
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender, registry)
    event = RoutedEvent('e1')

    async def call_next() -> None:  # noqa: RUF029
        collector.record([event])

    await behavior.handle(object(), call_next)

    assert sender.invoked == [event]
    assert outgoing.published == []


async def test_handler_failure_forwards_nothing() -> None:
    # Forwarding runs only AFTER call_next succeeds; a raising handler must forward nothing (the
    # torn-write guarantee at the behavior level, independent of the cascade's frame-discard).
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([RoutedEvent('e1')])
        error = 'boom'
        raise RuntimeError(error)

    with pytest.raises(RuntimeError, match='boom'):
        await behavior.handle(object(), call_next)

    assert outgoing.published == []
    assert sender.invoked == []
