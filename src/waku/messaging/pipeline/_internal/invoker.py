from typing import TYPE_CHECKING, Any

from waku.di import AsyncContainer
from waku.messages import IMessage
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.pipeline._internal.executor import PipelineExecutor
from waku.messaging.pipeline._internal.plan import BehaviorPlan

if TYPE_CHECKING:
    from waku.messaging.contracts.pipeline import IPipelineBehavior

__all__ = [
    'HandlerPipelineInvoker',
]


class HandlerPipelineInvoker:
    __slots__ = ('_plan',)

    def __init__(self, plan: BehaviorPlan) -> None:
        self._plan = plan

    async def invoke(
        self,
        scope: AsyncContainer,
        message: IMessage,
        handler_type: HandlerType,
    ) -> Any:
        handler = await scope.get(handler_type)
        behavior_types = self._plan.for_handler(handler_type)
        behaviors: list[IPipelineBehavior[Any, Any]] = [await scope.get(bt) for bt in behavior_types]
        return await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)
