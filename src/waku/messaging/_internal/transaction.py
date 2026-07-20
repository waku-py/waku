from __future__ import annotations

from typing import TYPE_CHECKING, Never, TypeVar, assert_never

from waku._internal.transaction import (
    Abort,
    Commit,
    TransactionDecision,
    TransactionExecution,
    require_committed,
)
from waku.exceptions import UnexpectedRollbackError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.messaging.contracts.pipeline import CallNext
    from waku.uow import IUnitOfWork

__all__ = ['TransactionDepth', 'decide_transaction', 'run_in_transaction']

_ResultT = TypeVar('_ResultT')


class TransactionDepth:
    """Per-scope transaction nesting counter for nesting-aware commit ownership.

    Shared by every ``TransactionalBehavior`` instance AND the dispatcher's owning frame
    within one DI scope. Registered ``scoped`` so each DI scope gets exactly one instance.
    The frame that takes depth 0 -> 1 owns commit/rollback (Spring ``PROPAGATION_REQUIRED``
    as a boundary depth-counter).

    ``rollback_cause`` carries Spring-strict rollback-only propagation: once a nested frame
    fails, the physical transaction is forced to roll back even if an outer handler catches
    the exception. The first failure is retained as the terminal cause.

    Plain ``__slots__`` class, NOT a dataclass: dishka would try to inject the state fields of
    a dataclass ``__init__``; a param-less ``__init__`` resolves cleanly.
    """

    __slots__ = ('depth', 'rollback_cause')

    def __init__(self) -> None:
        self.depth = 0
        self.rollback_cause: BaseException | None = None

    def enter(self) -> bool:
        """Increment depth; return True if this frame is the outermost (the owner)."""
        is_owner = self.depth == 0
        self.depth += 1
        return is_owner

    def mark_rollback_only(self, cause: BaseException) -> None:
        if self.rollback_cause is None:
            self.rollback_cause = cause

    def exit(self) -> None:
        self.depth -= 1
        if self.depth <= 0:
            # Clamp + reset so a partial failure cannot leave non-zero depth or a stale cause
            # poisoning scope reuse. Guard via clamp, not assert.
            self.depth = 0
            self.rollback_cause = None


async def decide_transaction(
    depth: TransactionDepth,
    call_next: Callable[[], Awaitable[_ResultT]],
) -> TransactionDecision[_ResultT, Never]:
    """Translate messaging nesting and rollback-only state into a transaction decision."""
    is_owner = depth.enter()
    try:
        try:
            result = await call_next()
        except BaseException as error:
            depth.mark_rollback_only(error)
            raise

        if not is_owner:
            return Commit(result)

        cause = depth.rollback_cause
        if cause is None:
            return Commit(result)
        if not isinstance(cause, Exception):
            raise cause

        error = UnexpectedRollbackError('Transaction rolled back because a nested operation failed')
        error.__cause__ = cause
        return Abort(error)
    finally:
        depth.exit()


async def run_in_transaction(
    uow: IUnitOfWork,
    depth: TransactionDepth,
    call_next: CallNext[_ResultT],
) -> _ResultT:
    """Run *call_next* inside a single physical transaction owned by the outermost frame.

    Shared by ``TransactionalBehavior`` (wraps one handler) and ``MessageDispatcher``'s transactional
    lifecycle policy, which wraps every inline body it owns — one handler for a request or a targeted
    redispatch, or a whole N-handler fan-out, so those per-handler frames join one transaction. Only
    the frame that takes depth 0 -> 1 commits/rolls back; nested frames join and return their
    result. On any inner failure the frame records the first rollback cause so the owner rolls back
    even if an outer handler swallows the exception (Spring-strict). A normal outer return from a
    rollback-only transaction raises ``UnexpectedRollbackError`` instead of reporting success. The
    ``finally`` always decrements depth.

    """
    if depth.depth != 0:
        nested = await decide_transaction(depth, call_next)
        if isinstance(nested, Commit):
            return nested.value
        if isinstance(nested, Abort):
            raise nested.error
        assert_never(nested.value)

    return require_committed(await TransactionExecution(uow).execute(lambda: decide_transaction(depth, call_next)))
