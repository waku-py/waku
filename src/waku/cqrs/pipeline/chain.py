from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Generic

from waku.cqrs.contracts.request import RequestT, ResponseT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.cqrs.contracts.pipeline import IPipelineBehavior, NextHandlerType


class PipelineBehaviorWrapper(Generic[RequestT, ResponseT]):
    """Composes pipeline behaviors into a processing chain."""

    def __init__(self, behaviors: Sequence[IPipelineBehavior[RequestT, ResponseT]]) -> None:
        """Initialize the pipeline behavior chain.

        Args:
            behaviors: Sequence of pipeline behaviors to execute in order
        """
        self._behaviors = list(behaviors)  # Convert to list immediately

    def wrap(self, handle: NextHandlerType[RequestT, ResponseT]) -> NextHandlerType[RequestT, ResponseT]:
        """Create a pipeline that wraps the handler function with behaviors.

        Pipeline behaviors are executed in the order they are provided.

        Args:
            handle: The handler function to wrap with behaviors

        Returns:
            A function that executes the entire pipeline
        """
        for behavior in reversed(self._behaviors):
            handle = functools.partial(behavior.handle, next_handler=handle)

        return handle
