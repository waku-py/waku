from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.backends.testing import make_outbox_message

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from waku.messaging.outbox.models import OutboxMessage


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_outbox_tables) as session:
        yield session


@pytest.fixture
def make_message() -> Callable[..., OutboxMessage]:
    return make_outbox_message
