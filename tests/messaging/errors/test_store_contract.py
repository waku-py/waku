from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.testing import DeadLetterStoreContract

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.messaging.durability import IDeadLetterStore

# The exported conformance kit carries the behavioral contract; this suite subscribes the memory
# backend's faithful store and the SQLAlchemy adapter, pinning fake == real. SQLAlchemy-only
# concerns (SKIP LOCKED, concurrent claim) stay in sqla/.


class TestDeadLetterStoreContract(DeadLetterStoreContract):
    @pytest.fixture(params=['fake', 'sqlalchemy'])
    @override
    def dlq_store(self, request: pytest.FixtureRequest) -> IDeadLetterStore:
        # The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
        if request.param == 'fake':
            return InMemoryDeadLetterStore()
        session: AsyncSession = request.getfixturevalue('dlq_pg_session')
        return SqlAlchemyDeadLetterStore(session)
