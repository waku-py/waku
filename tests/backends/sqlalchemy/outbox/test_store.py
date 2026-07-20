from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku._internal.node import NodeId, NodeIdentity
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.nodes.store import SqlAlchemyNodeRegistry
from waku.backends.sqlalchemy.nodes.tables import bind_node_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables, outbox_messages_table
from waku.backends.testing import make_outbox_message
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.outbox.models import OutboxStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.messaging.outbox.models import OutboxMessage


_OWNER = NodeId('relay-1')


@asynccontextmanager
async def _prepared(pg_engine: AsyncEngine, *binders: Callable[[MetaData], object]) -> AsyncGenerator[None]:
    metadata = MetaData()
    for binder in binders:
        binder(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    try:
        yield
    finally:
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


def _dead_letter_for(message: OutboxMessage) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type=message.message_type,
        payload=message.payload,
        destination=message.destination,
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id=message.correlation_id,
        causation_id=message.causation_id,
        exc=RuntimeError('boom'),
        attempt=3,
        message_id=message.message_id,
    )


async def _attempt(store: SqlAlchemyOutboxStore, message: OutboxMessage, owner: NodeId, transition: str) -> bool:
    match transition:
        case 'dispatched':
            return await store.mark_dispatched(message.id, owner_id=owner)
        case 'failed':
            return await store.mark_failed(message.id, 'exhausted', next_retry_at=None, owner_id=owner)
        case 'discarded':
            return await store.mark_discarded(message.id, 'policy drop', owner_id=owner)
        case _:
            return await store.move_to_dead_letter(message.id, _dead_letter_for(message), owner_id=owner)


class TestSqlAlchemyOutboxStore:
    # Behavioral coverage (save/fetch/dedup/mark_*/head-of-queue/cleanup) lives in the cross-impl
    # contract suite (tests/messaging/outbox/test_store_contract.py, parametrized fake|sqlalchemy).
    # What remains here is the SQL-specific raw-column persistence check.
    @staticmethod
    async def test_mark_discarded_persists_status_and_error(
        pg_session: AsyncSession,
        make_message: Callable[..., OutboxMessage],
    ) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10, owner_id=_OWNER)
        await store.mark_discarded(fetched[0].id, 'transport gave up', owner_id=_OWNER)
        await pg_session.flush()

        # DISCARDED is terminal (never re-fetched) AND the status/last_error columns are persisted.
        assert await store.fetch_head_of_queue(batch_size=10, owner_id=_OWNER) == []
        status_stmt = select(outbox_messages_table.c.status, outbox_messages_table.c.last_error).where(
            outbox_messages_table.c.id == fetched[0].id,
        )
        row = (await pg_session.execute(status_stmt)).one()
        assert row.status == OutboxStatus.DISCARDED.value
        assert row.last_error == 'transport gave up'

    @staticmethod
    async def test_metadata_column_round_trips(
        pg_session: AsyncSession,
        make_message: Callable[..., OutboxMessage],
    ) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        meta_payload = {
            'message_version': 2,
            'timestamp': '2026-06-29T10:00:00+00:00',
            'headers': {'tenant': 'acme'},
            'scheduled_time': None,
            'expires_at': None,
        }
        msg = make_message(metadata=meta_payload)
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10, owner_id=_OWNER)

        assert fetched[0].metadata == meta_payload

    @staticmethod
    async def test_metadata_column_defaults_to_none(
        pg_session: AsyncSession,
        make_message: Callable[..., OutboxMessage],
    ) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10, owner_id=_OWNER)

        assert fetched[0].metadata is None


def test_outbox_ddl_column_is_metadata() -> None:
    assert 'metadata' in outbox_messages_table.c
    assert 'metadata_' not in outbox_messages_table.c


class TestRecoverContentionAcrossSessions:
    """The membership fence under real two-session contention (plan §9.1).

    Two genuinely concurrent DB sessions model two relays over one database. Relay A claims a message
    and dies; a separate live session B reclaims it once A leaves the registry. A's still-running
    relay must then be told it lost EVERY terminal disposition — a stale finalize under a reassigned
    row is the S1-regression shape, and only the ``owner_id`` fence (not row age) closes it.
    """

    @staticmethod
    @pytest.mark.parametrize('transition', ['dispatched', 'failed', 'discarded', 'dead_letter'])
    async def test_reassigned_message_rejects_stale_owner(pg_engine: AsyncEngine, transition: str) -> None:
        async with _prepared(pg_engine, bind_outbox_tables, bind_node_tables, bind_dead_letter_tables):
            message = make_outbox_message()
            # Seed: A and B are registered members; A claims the message and COMMITS (in flight on A).
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                registry = SqlAlchemyNodeRegistry(seed)
                await registry.register(
                    NodeIdentity(node_id=NodeId('node-a'), description='node-a'), capabilities=frozenset()
                )
                await registry.register(
                    NodeIdentity(node_id=NodeId('node-b'), description='node-b'), capabilities=frozenset()
                )
                store = SqlAlchemyOutboxStore(seed)
                await store.save_batch([message])
                claimed = await store.fetch_head_of_queue(batch_size=10, owner_id=NodeId('node-a'))
                assert [m.id for m in claimed] == [message.id]
                await seed.commit()

            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as sa,  # A's relay: still alive, now stale
                AsyncSession(pg_engine, expire_on_commit=False) as sb,  # B: the successor relay
            ):
                # A has died -> B evicts it from the registry, reclaims by membership, and COMMITS.
                async with sb.begin():
                    await sb.execute(text("SET LOCAL lock_timeout = '2s'"))
                    await SqlAlchemyNodeRegistry(sb).deregister(NodeId('node-a'))
                    assert await SqlAlchemyOutboxStore(sb).recover_abandoned() == 1
                    reclaimed = await SqlAlchemyOutboxStore(sb).fetch_head_of_queue(
                        batch_size=10, owner_id=NodeId('node-b')
                    )
                    assert [m.id for m in reclaimed] == [message.id]

                # A's stale relay, in its own live session, now attempts to finalize the reassigned row.
                async with sa.begin():
                    await sa.execute(text("SET LOCAL lock_timeout = '2s'"))
                    applied = await _attempt(SqlAlchemyOutboxStore(sa), message, NodeId('node-a'), transition)

            assert applied is False

            # Observe terminal state through the port alone: release B, reclaim, read with an observer.
            async with AsyncSession(pg_engine, expire_on_commit=False) as obs:
                async with obs.begin():
                    await SqlAlchemyNodeRegistry(obs).deregister(NodeId('node-b'))
                    await SqlAlchemyOutboxStore(obs).recover_abandoned()
                    await SqlAlchemyNodeRegistry(obs).register(
                        NodeIdentity(node_id=NodeId('observer'), description='observer'), capabilities=frozenset()
                    )
                    survivors = await SqlAlchemyOutboxStore(obs).fetch_head_of_queue(
                        batch_size=10, owner_id=NodeId('observer')
                    )
                    dead = await SqlAlchemyDeadLetterStore(obs).fetch()
                assert [(m.id, m.status, m.attempts, m.last_error) for m in survivors] == [
                    (message.id, OutboxStatus.PROCESSING, 0, None),
                ]
                assert list(dead) == []
