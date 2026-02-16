from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData

from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.sqlalchemy.store import SqlAlchemyCheckpointStore
from waku.eventsourcing.projection.sqlalchemy.tables import bind_checkpoint_tables

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.projection.interfaces import ICheckpointStore


@pytest.fixture(params=['in_memory', 'sqlalchemy'])
def checkpoint_store(request: pytest.FixtureRequest) -> ICheckpointStore:
    if request.param == 'in_memory':
        return InMemoryCheckpointStore()

    pg_session: AsyncSession = request.getfixturevalue('pg_session')
    metadata = MetaData()
    checkpoints_table = bind_checkpoint_tables(metadata)
    return SqlAlchemyCheckpointStore(session=pg_session, checkpoints_table=checkpoints_table)
