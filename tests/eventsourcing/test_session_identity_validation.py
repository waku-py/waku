from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.di import object_, scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore, make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork, shared_session
from waku.modules import DynamicModule
from waku.testing import create_test_app
from waku.uow import IUnitOfWork


def _fake_session() -> AsyncSession:
    # The identity check only compares sessions by `is`; it never queries — a sentinel suffices.
    return cast('AsyncSession', object())


def _sqla_es_module() -> DynamicModule:
    tables = bind_event_store_tables(MetaData())
    return EventSourcingModule.register(EventSourcingConfig(store=make_sqlalchemy_event_store(tables)))


async def test_event_store_and_uow_sharing_one_session_boots() -> None:
    async with (
        create_test_app(imports=[_sqla_es_module()], providers=[*shared_session(_fake_session)]) as app,
        app.container() as scope,
    ):
        store = await scope.get(IEventStore)
        uow = await scope.get(IUnitOfWork)
        assert isinstance(store, SqlAlchemyEventStore)
        assert isinstance(uow, SqlAlchemyUnitOfWork)
        assert store.session is uow.session


async def test_split_sessions_raise_at_startup() -> None:
    rogue_session = cast('AsyncSession', object())
    with pytest.raises(ImproperlyConfiguredError, match='different sessions'):
        async with create_test_app(
            imports=[_sqla_es_module()],
            providers=[
                scoped(AsyncSession, _fake_session),
                object_(SqlAlchemyUnitOfWork(rogue_session), provided_type=IUnitOfWork),
            ],
        ):
            pass  # pragma: no cover


async def test_non_sqla_store_skips_identity_check() -> None:
    # InMemoryEventStore exposes no session -> identity cannot be proven -> no-op (no spurious raise).
    async with create_test_app(
        imports=[EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore))],
        providers=[*shared_session(_fake_session)],
    ):
        pass


async def test_no_unit_of_work_skips_identity_check() -> None:
    # Event Sourcing configured without a UoW -> nothing to validate identity against.
    async with create_test_app(
        imports=[EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore))],
    ):
        pass


async def test_shared_session_helper_wires_uow_onto_one_session() -> None:
    async with (
        create_test_app(providers=[*shared_session(_fake_session)]) as app,
        app.container() as scope,
    ):
        uow = await scope.get(IUnitOfWork)
        session = await scope.get(AsyncSession)
        assert isinstance(uow, SqlAlchemyUnitOfWork)
        assert uow.session is session
