from __future__ import annotations

import uuid
from dataclasses import dataclass

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.messages import IEvent


@dataclass(frozen=True)
class OrderCreated(IEvent):
    order_id: str


@dataclass(frozen=True)
class ItemAdded(IEvent):
    item_name: str


@dataclass(frozen=True)
class OrderShipped(IEvent):
    tracking_number: str


def make_envelope(event: IEvent) -> EventEnvelope:
    return EventEnvelope(domain_event=event, idempotency_key=str(uuid.uuid4()))
