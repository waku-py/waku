from __future__ import annotations

from typing import Protocol, runtime_checkable

from typing_extensions import override


@runtime_checkable
class IUnitOfWork(Protocol):
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class NoOpUnitOfWork(IUnitOfWork):
    """Shared null UoW substituted at resolve seams when no real UoW is registered.

    Never registered under the ``IUnitOfWork`` DI key (that would silently defeat the UoW presence
    checks); provisioned only at resolve-or-noop seams (dispatcher ``invoke_event`` framing, executor
    dead-letter write), where ``commit``/``rollback`` no-op.
    """

    __slots__ = ()

    @override
    async def commit(self) -> None: ...

    @override
    async def rollback(self) -> None: ...
