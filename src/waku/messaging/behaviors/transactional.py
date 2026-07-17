from __future__ import annotations

from typing import TYPE_CHECKING, Any, Never, TypeVar, assert_never

from typing_extensions import override

from waku._internal.transaction import (
    Abort,
    Commit,
    TransactionDecision,
    TransactionExecution,
    require_committed,
)

# Runtime imports: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so these DI-injected types must resolve at runtime — not under TYPE_CHECKING.
from waku.exceptions import UnexpectedRollbackError
from waku.messaging._internal.transaction import TransactionDepth  # noqa: TC001
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.uow import IUnitOfWork  # noqa: TC001

__all__ = [
    'TransactionalBehavior',
]

_ResultT = TypeVar('_ResultT')

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


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

    Shared by ``TransactionalBehavior`` (wraps one handler) and ``MessageDispatcher.invoke_event``
    (wraps the whole N-handler fan-out, so the N per-handler frames join one transaction). Only
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


class TransactionalBehavior(IPipelineBehavior[Any, Any]):
    __slots__ = ('_depth', '_uow')

    def __init__(self, uow: IUnitOfWork, depth: TransactionDepth) -> None:
        self._uow = uow
        self._depth = depth

    @override
    async def handle(self, _message: Any, /, call_next: CallNext[Any]) -> Any:
        return await run_in_transaction(self._uow, self._depth, call_next)
