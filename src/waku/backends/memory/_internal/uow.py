from __future__ import annotations

from typing_extensions import override

from waku.uow import IUnitOfWork

__all__ = ['InMemoryUnitOfWork']


class InMemoryUnitOfWork(IUnitOfWork):
    """The memory backend's committer: in-memory stores apply writes immediately, so commit/rollback no-op.

    Deliberately registered under the ``IUnitOfWork`` DI key by ``MemoryBackend`` (unlike the
    resolve-seam-only ``NoOpUnitOfWork``): a memory resource has no transaction to stage, and the
    wiring stub's job is to let durable-configured apps boot without a database. Consequence: the
    conformance kit's append+forward rollback assertion is opted out (``supports_rollback=False``).
    """

    __slots__ = ()

    @override
    async def commit(self) -> None: ...

    @override
    async def rollback(self) -> None: ...
