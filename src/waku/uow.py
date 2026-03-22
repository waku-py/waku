from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IUnitOfWork(Protocol):
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
