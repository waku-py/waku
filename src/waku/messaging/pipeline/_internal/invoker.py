from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

from waku.messaging._internal.outbox_cascading import DeferredCascadeFlusher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging._internal.uow import resolve_uow
from waku.messaging.behaviors.transactional import TransactionalBehavior, run_in_transaction
from waku.messaging.pipeline._internal.executor import PipelineExecutor
from waku.messaging.pipeline._internal.plan import BehaviorPlan  # noqa: TC001 -- Dishka introspects __init__ at runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.di import AsyncContainer
    from waku.messages import IMessage
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior

__all__ = [
    'HandlerPipelineInvoker',
]

_ExecutionWrapper: TypeAlias = 'Callable[[CallNext[Any]], Awaitable[Any]]'


async def _execute_direct(call_next: CallNext[Any]) -> Any:
    return await call_next()


class HandlerPipelineInvoker:
    __slots__ = ('_plan',)

    def __init__(self, plan: BehaviorPlan) -> None:
        self._plan = plan

    async def invoke(
        self,
        scope: AsyncContainer,
        message: IMessage,
        handler_type: HandlerType,
        *,
        result_aware_transaction: bool = False,
        execution_wrapper: _ExecutionWrapper = _execute_direct,
    ) -> Any:
        handler = await scope.get(handler_type)
        behavior_types = self._plan.for_handler(handler_type)
        behaviors: list[IPipelineBehavior[Any, Any]] = [await scope.get(bt) for bt in behavior_types]

        async def execute_pipeline() -> Any:
            return await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)

        async def execute() -> Any:
            return await execution_wrapper(execute_pipeline)

        has_transaction = any(issubclass(behavior, TransactionalBehavior) for behavior in behavior_types)
        if not result_aware_transaction or not has_transaction:
            return await execute()

        uow = await resolve_uow(scope)
        depth = await scope.get(TransactionDepth)
        result = await run_in_transaction(
            uow,
            depth,
            execute,
            rollback_failure_is_primary=True,
        )
        if depth.depth == 0:
            flusher = await scope.get(DeferredCascadeFlusher)
            await flusher.flush()
        return result
