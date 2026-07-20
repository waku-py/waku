from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

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

    def has_transaction(self, handler_type: HandlerType) -> bool:
        return self._plan.has_transaction(handler_type)

    async def invoke(
        self,
        scope: AsyncContainer,
        message: IMessage,
        handler_type: HandlerType,
        *,
        execution_wrapper: _ExecutionWrapper = _execute_direct,
    ) -> Any:
        handler = await scope.get(handler_type)
        behavior_types = self._plan.for_handler(handler_type)
        behaviors: list[IPipelineBehavior[Any, Any]] = [await scope.get(bt) for bt in behavior_types]

        async def execute_pipeline() -> Any:
            return await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)

        return await execution_wrapper(execute_pipeline)
