from __future__ import annotations

import abc
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from typing_extensions import override

from waku.eventsourcing.contracts.aggregate import AggregateT
from waku.eventsourcing.repository import EventSourcedRepository  # noqa: TC001  # Dishka needs runtime access
from waku.integrations.eventsourcing_messaging._internal.retry import execute_with_optimistic_retry
from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT
from waku.messaging.handler import RequestHandler

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

__all__ = ['EventSourcedCommandHandler', 'EventSourcedVoidCommandHandler']

logger = logging.getLogger(__name__)


class EventSourcedCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, AggregateT, ResponseT],
):
    max_attempts: ClassVar[int] = 3

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if 'max_attempts' in cls.__dict__ and cls.max_attempts < 1:
            msg = f'{cls.__name__}.max_attempts must be >= 1, got {cls.max_attempts}'
            raise ValueError(msg)

    def __init__(
        self,
        repository: EventSourcedRepository[AggregateT],
    ) -> None:
        self._repository = repository

    async def handle(self, request: RequestT, /) -> ResponseT:
        aggregate_id: str = self._aggregate_id(request)
        is_creation: bool = self._is_creation_command(request)
        logger.debug('Handling %s for %s', type(request).__name__, aggregate_id)

        async def _attempt() -> ResponseT:
            if is_creation:
                aggregate = self._repository.create_aggregate()
            else:
                aggregate = await self._repository.load(aggregate_id)

            idempotency_key = self._idempotency_key(request, aggregate.version)
            await self._execute(request, aggregate)

            # Appended events are forwarded to the outbox by EventForwardingBehavior (the store records
            # them into the scoped collector during save) — no in-handler publish (that was the torn-write).
            await self._repository.save(
                aggregate_id,
                aggregate,
                idempotency_key=idempotency_key,
            )

            return self._to_response(aggregate)

        return await execute_with_optimistic_retry(
            _attempt,
            max_attempts=self.max_attempts,
            is_creation=is_creation,
            request_name=type(request).__name__,
            aggregate_id=aggregate_id,
            attempt_context=self._create_attempt_context,
        )

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    async def _execute(self, request: RequestT, aggregate: AggregateT) -> None: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

    def _idempotency_key(self, request: RequestT, version: int) -> str | None:  # noqa: ARG002, PLR6301
        """Return a deduplication token for idempotent event appends.

        Args:
            request: The incoming command request.
            version: Stream version at load time (``-1`` for creation commands).
        """
        return None

    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:  # noqa: PLR6301
        """Return a new context manager for a single retry attempt.

        Called once per attempt — must return a fresh instance each time.
        """
        return nullcontext()

    @abc.abstractmethod
    def _to_response(self, aggregate: AggregateT) -> ResponseT: ...


class EventSourcedVoidCommandHandler(
    EventSourcedCommandHandler[RequestT, AggregateT, None],
    abc.ABC,
    Generic[RequestT, AggregateT],
):
    @override
    def _to_response(self, aggregate: AggregateT) -> None:
        return None
