from collections.abc import Sequence
from typing import Any

from dishka.exceptions import NoFactoryError

from waku.di import AsyncContainer
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.contracts.message import IMessage
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.pipeline.executor import PipelineExecutor
from waku.messaging.registry import MessageRegistry

__all__ = [
    'HandlerPipelineInvoker',
]


class HandlerPipelineInvoker:
    __slots__ = ('_registry',)

    def __init__(self, registry: MessageRegistry) -> None:
        self._registry = registry

    async def invoke(
        self,
        scope: AsyncContainer,
        message: IMessage,
        handler_type: HandlerType,
    ) -> Any:
        handler = await scope.get(handler_type)
        behaviors = await self._resolve_behaviors(scope, type(message))
        return await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)

    async def _resolve_behaviors(
        self,
        scope: AsyncContainer,
        message_type: type[IMessage],
    ) -> Sequence[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await scope.get(Sequence[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = ()

        if not self._registry.behavior_map.has_behaviors(message_type):
            return global_behaviors

        lookup_type = self._registry.behavior_map.get_lookup_type(message_type)
        scoped_behaviors = await scope.get(Sequence[lookup_type])  # type: ignore[valid-type]

        return (*global_behaviors, *scoped_behaviors)
