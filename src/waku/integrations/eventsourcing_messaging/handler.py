from __future__ import annotations

import abc
import logging
from typing import Generic

from typing_extensions import override

from waku.eventsourcing.contracts.aggregate import AggregateT
from waku.eventsourcing.forwarding import IAppendedEvents  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.repository import EventSourcedRepository  # noqa: TC001  # Dishka needs runtime access
from waku.integrations.eventsourcing_messaging._internal.command_handler import OptimisticRetryCommandHandler
from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT

__all__ = ['EventSourcedCommandHandler', 'EventSourcedVoidCommandHandler']

logger = logging.getLogger(__name__)


class EventSourcedCommandHandler(
    OptimisticRetryCommandHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, AggregateT, ResponseT],
):
    def __init__(
        self,
        repository: EventSourcedRepository[AggregateT],
        appended: IAppendedEvents,
    ) -> None:
        super().__init__(appended)
        self._repository = repository

    @override
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

        return await self._run_with_retry(
            _attempt,
            request_name=type(request).__name__,
            aggregate_id=aggregate_id,
            is_creation=is_creation,
        )

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    async def _execute(self, request: RequestT, aggregate: AggregateT) -> None: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

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
