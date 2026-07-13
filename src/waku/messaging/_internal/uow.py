from __future__ import annotations

from typing import TYPE_CHECKING

from dishka.exceptions import NoFactoryError
from typing_extensions import override

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

__all__ = ['NoOpUnitOfWork', 'resolve_uow']


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


async def resolve_uow(scope: AsyncContainer) -> IUnitOfWork:
    """Resolve the registered :class:`IUnitOfWork`, or a :class:`NoOpUnitOfWork` when none exists.

    Null-provisioning seam (not the doctrine's target): a real UoW when registered, else the null
    UoW. The noop is NOT put on the ``IUnitOfWork`` DI key — that would defeat the UoW presence checks.
    """
    try:
        uow: IUnitOfWork = await scope.get(IUnitOfWork)
    except NoFactoryError:
        return NoOpUnitOfWork()
    return uow
