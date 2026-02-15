from __future__ import annotations

from dataclasses import dataclass

from waku.eventsourcing.contracts.event import EventEnvelope


@dataclass(frozen=True)
class OrderCreated:
    order_id: str


@dataclass(frozen=True)
class ItemAdded:
    item_name: str


def make_envelope(event: object) -> EventEnvelope:
    return EventEnvelope(domain_event=event)
