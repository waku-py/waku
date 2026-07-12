from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku import DynamicModule
from waku.di import object_, scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.eventsourcing.projection.interfaces import ICheckpointStore
from waku.eventsourcing.projection.sqlalchemy.store import SqlAlchemyCheckpointStore, make_sqlalchemy_checkpoint_store
from waku.eventsourcing.projection.sqlalchemy.tables import bind_checkpoint_tables
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore
from waku.eventsourcing.snapshot.sqlalchemy.store import SqlAlchemySnapshotStore, make_sqlalchemy_snapshot_store
from waku.eventsourcing.snapshot.sqlalchemy.tables import bind_snapshot_tables
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore, make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.exceptions import ImproperlyConfiguredError
from waku.integrations.eventsourcing_messaging import EventSourcingMessagingModule
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork, shared_session
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW
from tests.messaging.outbox.fake_store import FakeOutboxStore


def _fake_session() -> AsyncSession:
    # The identity check only compares sessions by `is`; it never queries — a sentinel suffices.
    return cast('AsyncSession', object())


def _make_outbox_store(session: AsyncSession) -> SqlAlchemyOutboxStore:
    # SqlAlchemyOutboxStore imports AsyncSession only under TYPE_CHECKING, so dishka cannot introspect
    # the class directly; a local factory carries the runtime hint (mirrors make_sqlalchemy_*_store).
    return SqlAlchemyOutboxStore(session)


def _sqla_es_imports() -> list[DynamicModule]:
    # Atomic append+forward requires the event store and UoW to share one session; the integration
    # module contributes that check, so it is composed alongside the ES module.
    tables = bind_event_store_tables(MetaData())
    return [
        EventSourcingModule.register(EventSourcingConfig(store=make_sqlalchemy_event_store(tables))),
        EventSourcingMessagingModule.register(),
    ]


def _in_memory_es_imports() -> list[DynamicModule]:
    return [
        EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore)),
        EventSourcingMessagingModule.register(),
    ]


async def test_event_store_and_uow_sharing_one_session_boots() -> None:
    async with (
        create_test_app(imports=_sqla_es_imports(), providers=[*shared_session(_fake_session)]) as app,
        app.container() as scope,
    ):
        store = await scope.get(IEventStore)
        uow = await scope.get(IUnitOfWork)
        assert isinstance(store, SqlAlchemyEventStore)
        assert isinstance(uow, SqlAlchemyUnitOfWork)
        assert store.session is uow.session


async def test_split_sessions_raise_at_startup() -> None:
    rogue_session = cast('AsyncSession', object())
    with pytest.raises(ImproperlyConfiguredError, match='IEventStore'):
        async with create_test_app(
            imports=_sqla_es_imports(),
            providers=[
                scoped(AsyncSession, _fake_session),
                object_(SqlAlchemyUnitOfWork(rogue_session), provided_type=IUnitOfWork),
            ],
        ):
            pass  # pragma: no cover


async def test_snapshot_store_sharing_session_boots() -> None:
    async with create_test_app(
        imports=_sqla_es_imports(),
        providers=[
            *shared_session(_fake_session),
            scoped(ISnapshotStore, make_sqlalchemy_snapshot_store(bind_snapshot_tables(MetaData()))),
        ],
    ):
        pass


async def test_snapshot_store_split_session_raises() -> None:
    rogue_session = cast('AsyncSession', object())
    with pytest.raises(ImproperlyConfiguredError, match='ISnapshotStore'):
        async with create_test_app(
            imports=_sqla_es_imports(),
            providers=[
                *shared_session(_fake_session),
                object_(
                    SqlAlchemySnapshotStore(rogue_session, bind_snapshot_tables(MetaData())),
                    provided_type=ISnapshotStore,
                ),
            ],
        ):
            pass  # pragma: no cover


async def test_checkpoint_store_sharing_session_boots() -> None:
    async with create_test_app(
        imports=_sqla_es_imports(),
        providers=[
            *shared_session(_fake_session),
            scoped(ICheckpointStore, make_sqlalchemy_checkpoint_store(bind_checkpoint_tables(MetaData()))),
        ],
    ):
        pass


async def test_checkpoint_store_split_session_raises() -> None:
    rogue_session = cast('AsyncSession', object())
    with pytest.raises(ImproperlyConfiguredError, match='ICheckpointStore'):
        async with create_test_app(
            imports=_sqla_es_imports(),
            providers=[
                *shared_session(_fake_session),
                object_(
                    SqlAlchemyCheckpointStore(rogue_session, bind_checkpoint_tables(MetaData())),
                    provided_type=ICheckpointStore,
                ),
            ],
        ):
            pass  # pragma: no cover


async def test_outbox_store_sharing_session_boots() -> None:
    async with create_test_app(
        imports=_sqla_es_imports(),
        providers=[
            *shared_session(_fake_session),
            scoped(IOutboxStore, _make_outbox_store),
        ],
    ):
        pass


async def test_outbox_store_split_session_raises() -> None:
    rogue_session = cast('AsyncSession', object())
    with pytest.raises(ImproperlyConfiguredError, match='IOutboxStore'):
        async with create_test_app(
            imports=_sqla_es_imports(),
            providers=[
                *shared_session(_fake_session),
                object_(SqlAlchemyOutboxStore(rogue_session), provided_type=IOutboxStore),
            ],
        ):
            pass  # pragma: no cover


async def test_registered_store_without_session_is_skipped() -> None:
    # A registered store that exposes no `session` (FakeOutboxStore) cannot prove identity -> no-op.
    async with create_test_app(
        imports=_sqla_es_imports(),
        providers=[
            *shared_session(_fake_session),
            object_(FakeOutboxStore(), provided_type=IOutboxStore),
        ],
    ):
        pass


async def test_non_sqla_uow_skips_identity_check() -> None:
    # A UoW that exposes no session -> identity cannot be proven -> return before touching any store.
    async with create_test_app(
        imports=_sqla_es_imports(),
        providers=[
            scoped(AsyncSession, _fake_session),
            object_(FakeUoW(), provided_type=IUnitOfWork),
        ],
    ):
        pass


async def test_non_sqla_store_skips_identity_check() -> None:
    # InMemoryEventStore exposes no session -> identity cannot be proven -> no-op (no spurious raise).
    async with create_test_app(
        imports=_in_memory_es_imports(),
        providers=[*shared_session(_fake_session)],
    ):
        pass


async def test_no_unit_of_work_skips_identity_check() -> None:
    # Event Sourcing configured without a UoW -> nothing to validate identity against.
    async with create_test_app(imports=_in_memory_es_imports()):
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
