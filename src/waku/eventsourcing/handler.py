from __future__ import annotations

import abc
import logging
from typing import ClassVar, Generic

from typing_extensions import TypeVar, override

from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access
from waku.cqrs.requests.handler import RequestHandler
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.exceptions import ConcurrencyConflictError
from waku.eventsourcing.repository import EventSourcedRepository  # noqa: TC001  # Dishka needs runtime access

__all__ = ['EventSourcedCommandHandler', 'EventSourcedVoidCommandHandler']

logger = logging.getLogger(__name__)

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate, default=EventSourcedAggregate)


class EventSourcedCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT, AggregateT],
):
    max_attempts: ClassVar[int] = 3

    def __init__(
        self,
        repository: EventSourcedRepository[AggregateT],
        publisher: IPublisher,
    ) -> None:
        self._repository = repository
        self._publisher = publisher

    async def handle(self, request: RequestT, /) -> ResponseT:
        aggregate_id = self._aggregate_id(request)
        logger.debug('Handling %s for %s', type(request).__name__, aggregate_id)

        is_creation = self._is_creation_command(request)
        last_error: ConcurrencyConflictError | None = None

        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                if is_creation:
                    raise last_error  # type: ignore[misc]
                logger.info(
                    'Retrying %s for %s (attempt %d/%d) after concurrency conflict',
                    type(request).__name__,
                    aggregate_id,
                    attempt,
                    self.max_attempts,
                )

            if is_creation:
                aggregate = self._repository.create_aggregate()
            else:
                aggregate = await self._repository.load(aggregate_id)

            await self._execute(request, aggregate)

            try:
                _, events = await self._repository.save(
                    aggregate_id,
                    aggregate,
                    idempotency_key=self._idempotency_key(request),
                )
            except ConcurrencyConflictError as exc:
                last_error = exc
                continue

            for event in events:
                await self._publisher.publish(event)

            return self._to_response(aggregate)

        raise last_error  # type: ignore[misc]

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    async def _execute(self, request: RequestT, aggregate: AggregateT) -> None: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

    def _idempotency_key(self, request: RequestT) -> str | None:  # noqa: ARG002, PLR6301
        return None

    @abc.abstractmethod
    def _to_response(self, aggregate: AggregateT) -> ResponseT: ...


class EventSourcedVoidCommandHandler(
    EventSourcedCommandHandler[RequestT, None, AggregateT],
    abc.ABC,
    Generic[RequestT, AggregateT],
):
    @override
    def _to_response(self, aggregate: AggregateT) -> None:
        return None
