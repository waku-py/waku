from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dishka import AsyncContainer

__all__ = ['TransactionDepth', 'unit_of_work_scope']


class TransactionDepth:
    """Per-scope transaction nesting counter for nesting-aware commit ownership.

    Shared by every ``TransactionalBehavior`` instance AND the dispatcher's owning frame
    within one DI scope. Registered ``scoped`` so each DI scope gets exactly one instance.
    The frame that takes depth 0 -> 1 owns commit/rollback (Spring ``PROPAGATION_REQUIRED``
    as a boundary depth-counter).

    ``rollback_only`` carries Spring-strict rollback-only propagation: once a nested frame
    fails, the physical transaction is forced to roll back even if an outer handler catches
    the exception.

    Plain ``__slots__`` class, NOT a dataclass: dishka would try to inject the ``int``/``bool``
    fields of a dataclass ``__init__``; a param-less ``__init__`` resolves cleanly.
    """

    __slots__ = ('depth', 'rollback_only')

    def __init__(self) -> None:
        self.depth = 0
        self.rollback_only = False

    def enter(self) -> bool:
        """Increment depth; return True if this frame is the outermost (the owner)."""
        is_owner = self.depth == 0
        self.depth += 1
        return is_owner

    def exit(self) -> None:
        self.depth -= 1
        if self.depth <= 0:
            # Clamp + reset so a partial failure cannot leave a non-zero depth or a stale
            # rollback_only flag poisoning scope reuse. Guard via clamp, not assert
            # (assert is tests-only per feedback_no_assert_in_prod).
            self.depth = 0
            self.rollback_only = False


@asynccontextmanager
async def unit_of_work_scope(container: AsyncContainer) -> AsyncGenerator[AsyncContainer]:
    """Open a request scope, yield it, commit on clean exit / roll back on exception.

    Centralizes the 'scope owner commits' invariant for background workers and one-shot writes.

    Yields:
        The request-scoped container; resolve ``IUnitOfWork`` (and any scoped store) from it inside the
        ``async with`` block.
    """
    async with container() as scope:
        uow = await scope.get(IUnitOfWork)
        try:
            yield scope
        except Exception:
            await uow.rollback()
            raise
        else:
            await uow.commit()
