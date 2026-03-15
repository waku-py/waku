from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Generic, TypeAlias

from waku.messaging.contracts.message import MessageT, ResponseT

__all__ = [
    'CallNext',
    'IPipelineBehavior',
]

CallNext: TypeAlias = Callable[[], Awaitable[ResponseT]]


class IPipelineBehavior(ABC, Generic[MessageT, ResponseT]):
    @abstractmethod
    async def handle(
        self,
        message: MessageT,
        /,
        call_next: CallNext[ResponseT],
    ) -> ResponseT: ...
