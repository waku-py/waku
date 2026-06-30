from __future__ import annotations

from typing import TYPE_CHECKING

from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class TestSqlAlchemyUnitOfWork:
    @staticmethod
    async def test_commit_delegates_to_session(mocker: MockerFixture) -> None:
        session = mocker.AsyncMock()
        uow = SqlAlchemyUnitOfWork(session)

        await uow.commit()

        session.commit.assert_awaited_once()

    @staticmethod
    async def test_rollback_delegates_to_session(mocker: MockerFixture) -> None:
        session = mocker.AsyncMock()
        uow = SqlAlchemyUnitOfWork(session)

        await uow.rollback()

        session.rollback.assert_awaited_once()
