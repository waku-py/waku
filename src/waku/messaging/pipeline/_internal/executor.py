from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messages import IMessage
    from waku.messaging.contracts.pipeline import IPipelineBehavior

__all__ = [
    'PipelineExecutor',
]

_ResponseT_co = TypeVar('_ResponseT_co', covariant=True)
_ResponseT = TypeVar('_ResponseT')


class _MessageHandler(Protocol[_ResponseT_co]):
    async def handle(self, message: Any, /) -> _ResponseT_co: ...


class PipelineExecutor:
    @staticmethod
    async def execute(
        *,
        message: IMessage,
        handler: _MessageHandler[_ResponseT],
        behaviors: Sequence[IPipelineBehavior[Any, _ResponseT]],
    ) -> _ResponseT:
        async def terminal() -> _ResponseT:
            return await handler.handle(message)

        if not behaviors:
            return await terminal()

        async def step(idx: int) -> _ResponseT:
            if idx >= len(behaviors):
                return await terminal()
            return await behaviors[idx].handle(
                message,
                call_next=lambda: step(idx + 1),
            )

        return await step(0)
