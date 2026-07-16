from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.messaging.outbox.models import OutboxMessage

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_outbox_tables) as session:
        yield session


@pytest.fixture
def make_message() -> Callable[..., OutboxMessage]:
    def _make(**overrides: object) -> OutboxMessage:
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

    return _make
