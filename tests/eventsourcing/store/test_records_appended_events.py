from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import MetaData

from waku.backends.sqlalchemy.event_store.store import SqlAlchemyEventStore
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.eventsourcing.forwarding import AppendedEventsCollector
from waku.eventsourcing.serialization.interfaces import IEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.serialization.upcasting.chain import UpcasterChain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def test_in_memory_store_reports_not_recording() -> None:
    assert InMemoryEventStore(EventTypeRegistry()).records_appended_events is False


def test_sqlalchemy_store_records_flag_reflects_collector() -> None:
    tables = bind_event_store_tables(MetaData())
    without_collector = SqlAlchemyEventStore(
        cast('AsyncSession', object()),
        cast('IEventSerializer', object()),
        EventTypeRegistry(),
        tables,
        UpcasterChain({}),
        appended_events=None,
    )
    with_collector = SqlAlchemyEventStore(
        cast('AsyncSession', object()),
        cast('IEventSerializer', object()),
        EventTypeRegistry(),
        tables,
        UpcasterChain({}),
        appended_events=AppendedEventsCollector(),
    )

    assert without_collector.records_appended_events is False
    assert with_collector.records_appended_events is True
