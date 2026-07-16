from __future__ import annotations

__all__ = ['TransactionDepth']


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
