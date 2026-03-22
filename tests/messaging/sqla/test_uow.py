from __future__ import annotations

from unittest.mock import AsyncMock

from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork


class TestSqlAlchemyUnitOfWork:
    @staticmethod
    async def test_commit_delegates_to_session() -> None:
        session = AsyncMock()
        uow = SqlAlchemyUnitOfWork(session)

        await uow.commit()

        session.commit.assert_awaited_once()

    @staticmethod
    async def test_rollback_delegates_to_session() -> None:
        session = AsyncMock()
        uow = SqlAlchemyUnitOfWork(session)

        await uow.rollback()

        session.rollback.assert_awaited_once()
