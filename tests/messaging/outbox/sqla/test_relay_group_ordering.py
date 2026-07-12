from __future__ import annotations

# Runtime import: dishka introspects the `session` provider's return annotation via get_type_hints.
from collections.abc import AsyncIterator  # noqa: TC003
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
from dishka import Provider, Scope, make_async_container, provide
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku.messaging.errors.sqla.tables import bind_dead_letter_tables
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import bind_outbox_tables
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
from waku.messaging.transport._internal.registry import TransportRegistry
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import StubSubscription, make_relay_evaluator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.messaging.transport.inbound import ConsumeCallback

# End-to-end acceptance test for cluster-wide per-group FIFO under two concurrent relays sharing one
# PostgreSQL engine. Reverting only the `head_eligible` predicate in SqlAlchemyOutboxStore makes relay-B
# promote and send G.seq2 while G.seq1 is gated (out of order) -> relay-B claims 1 (not 0) and the final
# order assertion breaks. (Confirm once via `git stash push -- src/.../outbox/sqla/store.py`.)


def _make_message(**overrides: object) -> OutboxMessage:
    defaults = {
        'id': uuid4(),
        'idempotency_key': str(uuid4()),
        'message_type': 'test.Event',
        'payload': {'test': True},
        'destination': 'test://dest',
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
    }
    return OutboxMessage(**(defaults | overrides))  # type: ignore[arg-type]


class _GatedOrderingTransport(ITransport):
    """ITransport double that records send order and gates the head send on an event.

    The send whose ``metadata.message_id == gated_message_id`` sets ``gated_send_entered`` then blocks on
    ``gate``, so a second relay can be driven while the head is in flight; later sends record immediately.
    """

    def __init__(self, *, gated_message_id: str) -> None:
        self._gated_message_id = gated_message_id
        self.gate = anyio.Event()
        self.gated_send_entered = anyio.Event()
        self.sent_order: list[str] = []

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        if metadata.message_id == self._gated_message_id:
            self.gated_send_entered.set()
            await self.gate.wait()
        self.sent_order.append(metadata.message_id)

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        return StubSubscription()  # pragma: no cover -- relay only sends

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


class _PgRelayProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, engine: AsyncEngine, transport: ITransport) -> None:
        super().__init__()
        self._engine = engine
        self._registry = TransportRegistry({'test': transport})

    @provide
    async def session(self) -> AsyncIterator[AsyncSession]:
        # Fresh session (NullPool -> fresh connection) per REQUEST scope, so each relay's _process_batch
        # genuinely races the other on the shared DB row lock.
        session = AsyncSession(self._engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    # staticmethod factories: the stores' AsyncSession import is TYPE_CHECKING-only, so dishka cannot
    # introspect their __init__ — a factory taking the injected session is required.
    @provide(scope=Scope.REQUEST)
    @staticmethod
    def outbox_store(session: AsyncSession) -> IOutboxStore:
        return SqlAlchemyOutboxStore(session)

    @provide(scope=Scope.REQUEST)
    @staticmethod
    def uow(session: AsyncSession) -> IUnitOfWork:
        return SqlAlchemyUnitOfWork(session)

    @provide(scope=Scope.APP)
    def transport_registry(self) -> TransportRegistry:
        return self._registry


async def test_two_relays_dispatch_group_in_order(pg_engine: AsyncEngine) -> None:
    metadata = MetaData()
    bind_outbox_tables(metadata)
    bind_dead_letter_tables(metadata)  # a dispatch failure would write a dead-letter row
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    try:
        seq_ids = [uuid4(), uuid4(), uuid4()]
        async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
            await SqlAlchemyOutboxStore(seed).save_batch([
                _make_message(idempotency_key=str(mid), group_id='G', sequence_number=i + 1)
                for i, mid in enumerate(seq_ids)
            ])
            await seed.commit()

        transport = _GatedOrderingTransport(gated_message_id=str(seq_ids[0]))
        config = OutboxRelayConfig()
        async with make_async_container(_PgRelayProvider(pg_engine, transport)) as container:
            relay_a = OutboxRelay(
                container=container,
                config=config,
                sending_failure_evaluator=make_relay_evaluator(config),
            )
            relay_b = OutboxRelay(
                container=container,
                config=config,
                sending_failure_evaluator=make_relay_evaluator(config),
            )

            async with anyio.create_task_group() as tg:
                tg.start_soon(relay_a._process_batch)  # noqa: SLF001
                await wait_until(transport.gated_send_entered.is_set)
                # G.seq1 is a committed PROCESSING head, so relay-B is blocked on group G: it claims and
                # sends nothing while the predecessor is in flight on relay-A.
                assert await relay_b._process_batch() == 0  # noqa: SLF001
                assert transport.sent_order == []
                transport.gate.set()

            # relay-A finished dispatching G.seq1; the successors now promote one at a time, in order.
            assert await relay_a._process_batch() == 1  # noqa: SLF001  -> G.seq2
            assert await relay_a._process_batch() == 1  # noqa: SLF001  -> G.seq3

        assert transport.sent_order == [str(seq_ids[0]), str(seq_ids[1]), str(seq_ids[2])]
    finally:
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
