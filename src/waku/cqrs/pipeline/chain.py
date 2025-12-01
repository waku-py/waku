from __future__ import annotations

from typing import TYPE_CHECKING, Generic

from waku.cqrs.contracts.request import RequestT, ResponseT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.cqrs.contracts.pipeline import IPipelineBehavior, NextHandlerType


class PipelineBehaviorWrapper(Generic[RequestT, ResponseT]):
    """Composes pipeline behaviors into a processing chain."""

    __slots__ = ('_behaviors',)

    def __init__(self, behaviors: Sequence[IPipelineBehavior[RequestT, ResponseT]]) -> None:
        self._behaviors = tuple(behaviors)

    def wrap(self, handle: NextHandlerType[RequestT, ResponseT]) -> NextHandlerType[RequestT, ResponseT]:
        """Create a pipeline that wraps the handler function with behaviors.

        Pipeline behaviors are executed in the order they are provided.

        Args:
            handle: The handler function to wrap with behaviors

        Returns:
            A function that executes the entire pipeline
        """
        if not self._behaviors:
            return handle

        behaviors = self._behaviors

        async def pipeline(request: RequestT) -> ResponseT:
            async def execute(req: RequestT, idx: int) -> ResponseT:
                if idx >= len(behaviors):
                    return await handle(req)
                return await behaviors[idx].handle(req, next_handler=lambda r: execute(r, idx + 1))

            return await execute(request, 0)

        return pipeline
