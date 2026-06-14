from __future__ import annotations

import logging
from typing import Any, TypeVar

from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior

# Runtime import: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so this DI-injected type must resolve at runtime — not under TYPE_CHECKING.
from waku.uow import IUnitOfWork  # noqa: TC001

logger = logging.getLogger(__name__)

_T = TypeVar('_T')


class _TransactionDepth:
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


async def _safe_rollback(uow: IUnitOfWork) -> None:
    try:
        await uow.rollback()
    except Exception:
        logger.exception('Rollback failed')


async def run_in_transaction(uow: IUnitOfWork, depth: _TransactionDepth, call_next: CallNext[_T]) -> _T:
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

    def __init__(self, uow: IUnitOfWork, depth: _TransactionDepth) -> None:
        self._uow = uow
        self._depth = depth

    async def handle(self, _message: Any, /, call_next: CallNext[Any]) -> Any:
        return await run_in_transaction(self._uow, self._depth, call_next)
