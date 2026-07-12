from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from typing_extensions import override

from waku.backends.sqlalchemy.checkpoint.store import SqlAlchemyCheckpointStore
from waku.backends.sqlalchemy.checkpoint.tables import bind_checkpoint_tables
from waku.backends.testing import CheckpointStoreContract
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.store.interfaces import ICheckpointStore


class TestCheckpointStoreContract(CheckpointStoreContract):
    @pytest.fixture(params=['in_memory', 'sqlalchemy'])
    @override
    def checkpoint_store(self, request: pytest.FixtureRequest) -> ICheckpointStore:
        if request.param == 'in_memory':
            return InMemoryCheckpointStore()
        pg_session: AsyncSession = request.getfixturevalue('pg_session')
        checkpoints_table = bind_checkpoint_tables(MetaData()).checkpoints
        return SqlAlchemyCheckpointStore(session=pg_session, checkpoints_table=checkpoints_table)
