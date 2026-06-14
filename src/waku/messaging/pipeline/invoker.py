from collections.abc import Sequence
from typing import Any

from dishka.exceptions import NoFactoryError

from waku.di import AsyncContainer
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.contracts.message import IMessage
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.pipeline.executor import PipelineExecutor

__all__ = [
    'HandlerPipelineInvoker',
]


class HandlerPipelineInvoker:
    __slots__ = ()

    async def invoke(
        self,
        scope: AsyncContainer,
        message: IMessage,
        handler_type: HandlerType,
    ) -> Any:
        handler = await scope.get(handler_type)
        behaviors = await self._resolve_behaviors(scope, handler_type)
        return await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)

    @staticmethod
    async def _resolve_behaviors(
        scope: AsyncContainer,
        handler_type: HandlerType,
    ) -> Sequence[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await scope.get(Sequence[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = ()

        # Per-handler behaviors from the ClassVar (direct access -> MRO inheritance).
        per_handler = [await scope.get(behavior_type) for behavior_type in handler_type.additional_behaviors]

        # global outer -> per-handler inner -> handler
        return (*global_behaviors, *per_handler)
