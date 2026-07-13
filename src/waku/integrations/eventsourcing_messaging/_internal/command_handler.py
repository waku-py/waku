from __future__ import annotations

import abc
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT
from waku.messaging.handler import RequestHandler

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager


class OptimisticRetryCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT],
):
    """Shared retry-contract surface for command handlers that save via optimistic concurrency."""

    max_attempts: ClassVar[int] = 3

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if 'max_attempts' in cls.__dict__ and cls.max_attempts < 1:
            msg = f'{cls.__name__}.max_attempts must be >= 1, got {cls.max_attempts}'
            raise ValueError(msg)

    def _idempotency_key(self, request: RequestT, version: int) -> str | None:  # noqa: ARG002, PLR6301
        """Return a deduplication token for idempotent event appends.

        Args:
            request: The incoming command request.
            version: Stream version at load time (``-1`` when the stream has no prior
                version — creation / new stream).
        """
        return None

    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:  # noqa: PLR6301
        """Return a new context manager for a single retry attempt.

        Called once per attempt — must return a fresh instance each time.
        """
        return nullcontext()
