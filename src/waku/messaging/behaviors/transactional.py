from __future__ import annotations

import logging
from typing import Any, TypeVar

from typing_extensions import override

# Runtime imports: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so these DI-injected types must resolve at runtime — not under TYPE_CHECKING.
from waku.messaging._internal.transaction import TransactionDepth  # noqa: TC001
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.uow import IUnitOfWork  # noqa: TC001

__all__ = [
    'TransactionalBehavior',
]

logger = logging.getLogger(__name__)

_ResultT = TypeVar('_ResultT')


async def _safe_rollback(uow: IUnitOfWork) -> None:
    try:
        await uow.rollback()
    except Exception:
        logger.exception('Rollback failed')


async def run_in_transaction(uow: IUnitOfWork, depth: TransactionDepth, call_next: CallNext[_ResultT]) -> _ResultT:
    """Run *call_next* inside a single physical transaction owned by the outermost frame.

    Shared by ``TransactionalBehavior`` (wraps one handler) and ``MessageDispatcher.invoke_event``
    (wraps the whole N-handler fan-out, so the N per-handler frames join one transaction). Only
    the frame that takes depth 0 -> 1 commits/rolls back; nested frames join and return their
    result. On any inner failure the frame marks ``rollback_only`` so the owner rolls back even if
    an outer handler swallows the exception (Spring-strict). The ``finally`` always decrements depth.
    """
    is_owner = depth.enter()
    try:
        try:
            result = await call_next()
        except Exception:
            depth.rollback_only = True
            if is_owner:
                await _safe_rollback(uow)
            raise
        if is_owner and depth.rollback_only:
            await _safe_rollback(uow)
            return result
        if is_owner:
            try:
                await uow.commit()
            except Exception:
                await _safe_rollback(uow)
                raise
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
