from __future__ import annotations

# Runtime import: dishka introspects __init__ type hints at container-build time (get_type_hints),
# so this DI-injected param type must resolve at runtime — the backend wiring
# scoped(IUnitOfWork, SqlAlchemyUnitOfWork) raises UndefinedTypeAnalysisError otherwise.
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from typing_extensions import override

from waku.uow import IUnitOfWork

__all__ = ['SqlAlchemyUnitOfWork']


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """The SQLAlchemy committer: commits/rolls back the backend's one scoped ``AsyncSession``."""

    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def commit(self) -> None:
        await self._session.commit()

    @override
    async def rollback(self) -> None:
        await self._session.rollback()
