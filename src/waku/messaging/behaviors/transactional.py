from __future__ import annotations

from typing import Any, TypeVar

from typing_extensions import override

from waku._internal.transaction import commit_uow, rollback_uow

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


async def run_in_transaction(
    uow: IUnitOfWork,
    depth: TransactionDepth,
    call_next: CallNext[_ResultT],
    *,
    rollback_failure_is_primary: bool = False,
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
    is_owner = depth.enter()
    try:
        try:
            result = await call_next()
        except BaseException as exc:
            depth.mark_rollback_only(exc)
            if is_owner:
                await rollback_uow(
                    uow,
                    primary_error=exc,
                    rollback_failure_is_primary=rollback_failure_is_primary,
                )
            raise

        cause = depth.rollback_cause
        if is_owner and cause is not None:
            if isinstance(cause, Exception):
                failure = UnexpectedRollbackError('Transaction rolled back because a nested operation failed')
                await rollback_uow(
                    uow,
                    primary_error=failure,
                    rollback_failure_is_primary=rollback_failure_is_primary,
                )
                raise failure from cause
            await rollback_uow(uow, primary_error=cause)
            raise cause

        if is_owner:
            await commit_uow(uow, rollback_failure_is_primary=rollback_failure_is_primary)
        return result
    finally:
        depth.exit()


class TransactionalBehavior(IPipelineBehavior[Any, Any]):
    __slots__ = ('_depth', '_uow')

    def __init__(self, uow: IUnitOfWork, depth: TransactionDepth) -> None:
        self._uow = uow
        self._depth = depth

    @override
    async def handle(self, _message: Any, /, call_next: CallNext[Any]) -> Any:
        return await run_in_transaction(self._uow, self._depth, call_next)
