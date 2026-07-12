from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.testing import OutboxStoreContract

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.messaging.durability import IOutboxStore

# The exported conformance kit carries the behavioral contract; this suite subscribes the memory
# backend's faithful store and the SQLAlchemy adapter, pinning fake == real. SQLAlchemy-only
# concerns (concurrent FOR UPDATE SKIP LOCKED) stay in sqla/.


class TestOutboxStoreContract(OutboxStoreContract):
    @pytest.fixture(params=['fake', 'sqlalchemy'])
    @override
    def outbox_store(self, request: pytest.FixtureRequest) -> IOutboxStore:
        # The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
        if request.param == 'fake':
            return InMemoryOutboxStore(InMemoryDeadLetterStore())
        session: AsyncSession = request.getfixturevalue('outbox_pg_session')
        return SqlAlchemyOutboxStore(session)
