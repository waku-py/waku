from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.messaging import MessagingConfig, MessagingModule
from waku.testing import create_test_app


async def test_register_with_metadata_binds_the_sequences_table_when_messaging_is_active() -> None:
    metadata = MetaData()

    def _session_factory() -> AsyncSession:  # pragma: no cover - never resolved
        return AsyncSession()

    async with create_test_app(
        imports=[
            MessagingModule.register(MessagingConfig()),
            SqlAlchemyBackend.register(session_factory=_session_factory, metadata=metadata),
        ],
    ):
        assert 'message_sequences' in metadata.tables
