from __future__ import annotations

from typing_extensions import override

from waku.backends.memory._internal.transaction import InMemoryTransactionWorkspace  # noqa: TC001  # DI introspection
from waku.uow import IUnitOfWork

__all__ = ['InMemoryUnitOfWork']


class InMemoryUnitOfWork(IUnitOfWork):
    """Commit or discard the memory backend's scoped transaction workspace."""

    __slots__ = ('_workspace',)

    def __init__(self, workspace: InMemoryTransactionWorkspace) -> None:
        self._workspace = workspace

    @override
    async def commit(self) -> None:
        await self._workspace.commit()

    @override
    async def rollback(self) -> None:
        await self._workspace.rollback()
