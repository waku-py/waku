from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.contracts.message import IMessage
    from waku.messaging.contracts.pipeline import IPipelineBehavior

__all__ = [
    'PipelineExecutor',
]

_T_co = TypeVar('_T_co', covariant=True)
_T = TypeVar('_T')


class _MessageHandler(Protocol[_T_co]):
    async def handle(self, message: Any, /) -> _T_co: ...


class PipelineExecutor:
    @staticmethod
    async def execute(
        *,
        message: IMessage,
        handler: _MessageHandler[_T],
        behaviors: Sequence[IPipelineBehavior[Any, _T]],
    ) -> _T:
        async def terminal() -> _T:
            return await handler.handle(message)

        if not behaviors:
            return await terminal()

        async def step(idx: int) -> _T:
            if idx >= len(behaviors):
                return await terminal()
            return await behaviors[idx].handle(
                message,
                call_next=lambda: step(idx + 1),
            )

        return await step(0)
