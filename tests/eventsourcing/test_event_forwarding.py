from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from typing_extensions import override

from waku.eventsourcing import forward
from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.forwarding import (
    AppendedEventsCollector,
    ForwardingRegistry,
)
from waku.integrations.eventsourcing_messaging.forwarding import EventForwardingBehavior
from waku.messages import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.interfaces import ISender
from waku.messaging.outgoing import IOutgoingMessages
from waku.messaging.router import MessageRouter

if TYPE_CHECKING:
    from datetime import timedelta

    from waku.messages import IMessage
    from waku.messaging.delivery import DeliveryOptions
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
    async def invoke(self, message: Any, /, options: DeliveryOptions | None = None) -> Any:
        self.invoked.append(message)
        return None

    @override
    async def send(self, message: IMessage, /, options: DeliveryOptions | None = None) -> None:  # pragma: no cover
        msg = 'forwarding must not send'
        raise AssertionError(msg)

    @override
    async def schedule_send(
        self,
        message: IMessage,
        /,
        *,
        at: datetime | None = None,
        delay: timedelta | None = None,
    ) -> None:  # pragma: no cover
        msg = 'forwarding must not schedule'
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


def _stored(
    event: IEvent,
    /,
    *,
    stream_id: StreamId | None = None,
    position: int = 0,
    global_position: int = 0,
) -> StoredEvent:
    return StoredEvent(
        event_id=uuid4(),
        stream_id=stream_id if stream_id is not None else StreamId.for_aggregate('Note', 'n-1'),
        event_type=type(event).__name__,
        position=position,
        global_position=global_position,
        timestamp=datetime.now(UTC),
        data=event,
        metadata=EventMetadata(),
        idempotency_key=str(uuid4()),
    )


async def test_routed_appended_event_forwarded_raw_to_outgoing() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender)
    event = RoutedEvent('e1')

    async def call_next() -> str:  # noqa: RUF029
        collector.record([_stored(event)])
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
        collector.record([_stored(UnroutedEvent('e1'))])

    await behavior.handle(object(), call_next)

    assert outgoing.published == []
    assert sender.invoked == []


async def test_registered_transform_forwards_integration_event() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    registry = ForwardingRegistry([
        forward(RoutedEvent).transformed_to(lambda s: IntegrationEvent(cast('RoutedEvent', s.data).note))
    ])
    behavior = _behavior(collector, outgoing, _router_for(IntegrationEvent), sender, registry)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([_stored(RoutedEvent('payload'))])

    await behavior.handle(object(), call_next)

    assert outgoing.published == [IntegrationEvent('payload')]
    assert sender.invoked == []


async def test_transform_receives_stream_provenance() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    registry = ForwardingRegistry([
        forward(RoutedEvent).transformed_to(lambda s: IntegrationEvent(note=str(s.stream_id)))
    ])
    behavior = _behavior(collector, outgoing, _router_for(IntegrationEvent), sender, registry)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([_stored(RoutedEvent('payload'), stream_id=StreamId.for_aggregate('Note', 'n-42'))])

    await behavior.handle(object(), call_next)

    assert outgoing.published == [IntegrationEvent(note=str(StreamId.for_aggregate('Note', 'n-42')))]
    assert sender.invoked == []


async def test_same_transaction_rule_invokes_inline_not_outbox() -> None:
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    registry = ForwardingRegistry([forward(RoutedEvent).same_transaction()])
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender, registry)
    event = RoutedEvent('e1')

    async def call_next() -> None:  # noqa: RUF029
        collector.record([_stored(event)])

    await behavior.handle(object(), call_next)

    assert sender.invoked == [event]
    assert outgoing.published == []


async def test_handler_failure_forwards_nothing() -> None:
    # Forwarding runs only AFTER call_next succeeds; a raising handler must forward nothing (the
    # torn-write guarantee at the behavior level, independent of the cascade's frame-discard).
    collector, outgoing, sender = AppendedEventsCollector(), _RecordingOutgoing(), _RecordingSender()
    behavior = _behavior(collector, outgoing, _router_for(RoutedEvent), sender)

    async def call_next() -> None:  # noqa: RUF029
        collector.record([_stored(RoutedEvent('e1'))])
        error = 'boom'
        raise RuntimeError(error)

    with pytest.raises(RuntimeError, match='boom'):
        await behavior.handle(object(), call_next)

    assert outgoing.published == []
    assert sender.invoked == []
