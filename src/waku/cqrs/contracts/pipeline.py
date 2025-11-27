from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Generic, TypeAlias

from waku.cqrs.contracts.request import RequestT, ResponseT

__all__ = [
    'IPipelineBehavior',
    'NextHandlerType',
]

NextHandlerType: TypeAlias = Callable[[RequestT], Awaitable[ResponseT]]


class IPipelineBehavior(ABC, Generic[RequestT, ResponseT]):
    """Interface for pipeline behaviors that wrap request handling."""

    @abstractmethod
    async def handle(self, request: RequestT, /, next_handler: NextHandlerType[RequestT, ResponseT]) -> ResponseT:
        """Handle the request and call the next handler in the pipeline.

        Args:
            request: The request to handle
            next_handler: Function to call the next handler in the pipeline

        Returns:
            The response from the pipeline
        """
        ...
