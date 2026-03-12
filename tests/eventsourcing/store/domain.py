from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.event import EventEnvelope

if TYPE_CHECKING:
    from waku.messaging import IEvent


@dataclass(frozen=True)
class OrderCreated:
    order_id: str


@dataclass(frozen=True)
class ItemAdded:
    item_name: str


@dataclass(frozen=True)
class OrderShipped:
    tracking_number: str


def make_envelope(event: IEvent) -> EventEnvelope:
    return EventEnvelope(domain_event=event, idempotency_key=str(uuid.uuid4()))
