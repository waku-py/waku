from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku.messaging import CallNext, IPipelineBehavior, IRequest, MessageT, ResponseT


@dataclass(frozen=True, slots=True)
class Cmd(IRequest[None]):
    value: str


class PassthroughBehavior(IPipelineBehavior[MessageT, ResponseT]):
    @override
    async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
        return await call_next()  # pragma: no cover -- plan tests never execute the chain


class SomeBehavior(PassthroughBehavior[Any, Any]): ...
