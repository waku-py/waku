from __future__ import annotations

import abc
from typing import Generic

from typing_extensions import TypeVar

from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access
from waku.cqrs.requests.handler import RequestHandler
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.repository import EventSourcedRepository  # noqa: TC001  # Dishka needs runtime access

__all__ = ['EventSourcedCommandHandler']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate, default=EventSourcedAggregate)


class EventSourcedCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT, AggregateT],
):
    """Command handler that orchestrates the event-sourced aggregate lifecycle.

    Loads or creates an aggregate, executes domain logic, persists new events,
    and publishes them through the CQRS pipeline.  Plugs into the standard
    ``MediatorExtension.bind_request()`` registration.
    """

    def __init__(
        self,
        repository: EventSourcedRepository[AggregateT],
        publisher: IPublisher,
    ) -> None:
        self._repository = repository
        self._publisher = publisher

    async def handle(self, request: RequestT, /) -> ResponseT:
        aggregate_id = self._aggregate_id(request)

        if self._is_creation_command(request):
            aggregate = self._repository.create_aggregate()
        else:
            aggregate = await self._repository.load(aggregate_id)

        await self._execute(request, aggregate)
        _, events = await self._repository.save(aggregate_id, aggregate)

        for event in events:
            await self._publisher.publish(event)

        return self._to_response(aggregate)

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    async def _execute(self, request: RequestT, aggregate: AggregateT) -> None: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

    def _to_response(self, aggregate: AggregateT) -> ResponseT:  # noqa: ARG002, PLR6301
        return None  # type: ignore[return-value]
