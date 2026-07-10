from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # dishka introspects the session factory return type

from waku.eventsourcing import forward
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.exceptions import ImproperlyConfiguredError
from waku.integrations.eventsourcing_messaging import EventSourcingMessagingModule
from waku.messages import IEvent
from waku.messaging.sqla.uow import shared_session
from waku.testing import create_test_app


@dataclass(frozen=True)
class _Ping(IEvent):
    pass


def _fake_session() -> AsyncSession:
    # The recording-store check never queries — a sentinel session suffices to build the store.
    return cast('AsyncSession', object())


async def test_forwarding_without_bridge_raises() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='EventSourcingMessagingModule'):
        async with create_test_app(
            imports=[
                EventSourcingModule.register(
                    EventSourcingConfig(store=InMemoryEventStore, forwarding=[forward(_Ping).same_transaction()]),
                ),
            ],
        ):
            pass  # pragma: no cover


async def test_forwarding_with_non_recording_store_raises() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='InMemoryEventStore'):
        async with create_test_app(
            imports=[
                EventSourcingModule.register(
                    EventSourcingConfig(store=InMemoryEventStore, forwarding=[forward(_Ping).same_transaction()]),
                ),
                EventSourcingMessagingModule.register(),
            ],
        ):
            pass  # pragma: no cover


async def test_forwarding_with_recording_store_and_bridge_boots() -> None:
    tables = bind_event_store_tables(MetaData())
    async with create_test_app(
        imports=[
            EventSourcingModule.register(
                EventSourcingConfig(
                    store=make_sqlalchemy_event_store(tables),
                    forwarding=[forward(_Ping).same_transaction()],
                ),
            ),
            EventSourcingMessagingModule.register(),
        ],
        providers=[*shared_session(_fake_session)],
    ):
        pass


async def test_empty_forwarding_skips_validation() -> None:
    async with create_test_app(
        imports=[EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore))],
    ):
        pass
