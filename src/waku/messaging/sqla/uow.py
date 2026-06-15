from __future__ import annotations

from typing import TYPE_CHECKING

# Runtime import: dishka introspects __init__ type hints at container-build time (get_type_hints),
# so this DI-injected param type must resolve at runtime — the paved-road wiring
# scoped(IUnitOfWork, SqlAlchemyUnitOfWork) raises UndefinedTypeAnalysisError otherwise.
# (Also used as a runtime value in shared_session below.)
from sqlalchemy.ext.asyncio import AsyncSession

from waku.di import Provider, scoped
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    'SqlAlchemyUnitOfWork',
    'shared_session',
]


class SqlAlchemyUnitOfWork(IUnitOfWork):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """The underlying AsyncSession — used for sharing-by-identity validation at startup."""
        return self._session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


def shared_session(session_factory: Callable[..., AsyncSession]) -> tuple[Provider, ...]:
    """Register one scoped AsyncSession shared by the event store, UoW, and outbox.

    Waku's ``Enroll(session)`` analog: a single ``scoped(AsyncSession)`` makes the event-store
    append, the UoW commit, and the outbox write run on ONE session within a request scope, so they
    commit atomically. Opt-in paved road — Waku stays BYO-session (register ``AsyncSession`` and
    ``IUnitOfWork`` yourself) by default.

    Args:
        session_factory: A provider for ``AsyncSession`` (its dependencies are injected by the container).

    Returns:
        Providers for ``scoped(AsyncSession)`` and ``scoped(IUnitOfWork, SqlAlchemyUnitOfWork)``.
    """
    return (
        scoped(AsyncSession, session_factory),
        scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
    )
