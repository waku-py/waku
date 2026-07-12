from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.testing import InboxStoreContract

from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.messaging.durability import IInboxStore

# The exported conformance kit carries the behavioral contract; this suite subscribes the canonical
# fake and the SQLAlchemy adapter, pinning fake == real. SQLAlchemy-only concerns (concurrent
# FOR UPDATE SKIP LOCKED) stay in sqla/.


class TestInboxStoreContract(InboxStoreContract):
    @pytest.fixture(params=['fake', 'sqlalchemy'])
    @override
    def inbox_store(self, request: pytest.FixtureRequest) -> IInboxStore:
        # The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
        if request.param == 'fake':
            return FakeInboxStore()
        session: AsyncSession = request.getfixturevalue('inbox_pg_session')
        return SqlAlchemyInboxStore(session)
