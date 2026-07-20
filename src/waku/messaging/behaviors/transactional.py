from __future__ import annotations

from typing import Any

from typing_extensions import override

# Runtime imports: dishka introspects __init__ type hints at container-build time (get_type_hints), so
# TransactionDepth and IUnitOfWork — both DI-injected constructor annotations below — must resolve at runtime,
# not under TYPE_CHECKING. TransactionDepth carries no `noqa: TC001` because ruff suppresses the rule
# module-wide once any name from this module is imported at runtime — here run_in_transaction, called in
# handle(); a defensive noqa would trip RUF100. Should run_in_transaction stop being imported here, TC001
# wakes up and its autofix would break dishka's container build — re-add a `noqa: TC001` suppression then.
from waku.messaging._internal.transaction import TransactionDepth, run_in_transaction
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.uow import IUnitOfWork  # noqa: TC001

__all__ = [
    'TransactionalBehavior',
]


class TransactionalBehavior(IPipelineBehavior[Any, Any]):
    __slots__ = ('_depth', '_uow')

    def __init__(self, uow: IUnitOfWork, depth: TransactionDepth) -> None:
        self._uow = uow
        self._depth = depth

    @override
    async def handle(self, _message: Any, /, call_next: CallNext[Any]) -> Any:
        return await run_in_transaction(self._uow, self._depth, call_next)
