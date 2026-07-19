from __future__ import annotations

import abc
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from waku.eventsourcing.forwarding import IAppendedEvents  # noqa: TC001  # Dishka needs runtime access
from waku.exceptions import ImproperlyConfiguredError
from waku.integrations.eventsourcing_messaging._internal.retry import execute_with_optimistic_retry
from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT
from waku.messaging.handler import RequestHandler

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager


class OptimisticRetryCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT],
):
    """Shared retry-contract surface for command handlers that save via optimistic concurrency."""

    max_attempts: ClassVar[int] = 3

    def __init__(self, appended: IAppendedEvents) -> None:
        self._appended = appended

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if 'max_attempts' in cls.__dict__ and cls.max_attempts < 1:
            msg = f'{cls.__name__}.max_attempts must be >= 1, got {cls.max_attempts}'
            raise ImproperlyConfiguredError(msg)

    async def _run_with_retry(
        self,
        attempt_fn: Callable[[], Awaitable[ResponseT]],
        *,
        request_name: str,
        aggregate_id: str,
        is_creation: bool = False,
    ) -> ResponseT:
        """Run ``attempt_fn`` under optimistic-concurrency retry with the appended-events reset bound in.

        Binding ``reset=self._appended.clear`` here — rather than at each call site — makes it structurally
        impossible for a concrete handler to build a retry loop that forgets to discard a prior attempt's
        accumulated events (the duplicate-forwarding regression BLK-1 guards against).
        """
        return await execute_with_optimistic_retry(
            attempt_fn,
            max_attempts=self.max_attempts,
            is_creation=is_creation,
            request_name=request_name,
            aggregate_id=aggregate_id,
            attempt_context=self._create_attempt_context,
            reset=self._appended.clear,
        )

    def _idempotency_key(self, request: RequestT, version: int) -> str | None:  # noqa: ARG002, PLR6301
        """Return a deduplication token for idempotent event appends.

        Args:
            request: The incoming command request.
            version: Stream version at load time (``-1`` when the stream has no prior
                version — creation / new stream).
        """
        return None

    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:  # noqa: PLR6301
        """Return a fresh per-attempt context manager (e.g. a transaction). Default: no-op."""
        return nullcontext()
