from __future__ import annotations

import abc
import logging
from typing import ClassVar, Generic

from typing_extensions import TypeVar, override

from waku.cqrs.contracts.notification import INotification
from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access
from waku.cqrs.requests.handler import RequestHandler
from waku.eventsourcing.contracts.aggregate import IDecider  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.decider.repository import DeciderRepository  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.exceptions import ConcurrencyConflictError

__all__ = ['DeciderCommandHandler', 'DeciderVoidCommandHandler']

logger = logging.getLogger(__name__)

StateT = TypeVar('StateT', default=object)
CommandT = TypeVar('CommandT', default=object)
EventT = TypeVar('EventT', bound=INotification, default=INotification)


class DeciderCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT, StateT, CommandT, EventT],
):
    max_attempts: ClassVar[int] = 3

    def __init__(
        self,
        repository: DeciderRepository[StateT, CommandT, EventT],
        decider: IDecider[StateT, CommandT, EventT],
        publisher: IPublisher,
    ) -> None:
        self._repository = repository
        self._decider = decider
        self._publisher = publisher

    async def handle(self, request: RequestT, /) -> ResponseT:
        aggregate_id = self._aggregate_id(request)
        command = self._to_command(request)
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
                state = self._decider.initial_state()
                version = -1
            else:
                state, version = await self._repository.load(aggregate_id)

            events = self._decider.decide(command, state)

            for event in events:
                state = self._decider.evolve(state, event)

            try:
                new_version = await self._repository.save(
                    aggregate_id,
                    events,
                    version,
                    current_state=state,
                    idempotency_key=self._idempotency_key(request),
                )
            except ConcurrencyConflictError as exc:
                last_error = exc
                continue

            for event in events:
                await self._publisher.publish(event)

            return self._to_response(state, new_version)

        raise last_error  # type: ignore[misc]

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    def _to_command(self, request: RequestT) -> CommandT: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

    def _idempotency_key(self, request: RequestT) -> str | None:  # noqa: ARG002, PLR6301
        return None

    @abc.abstractmethod
    def _to_response(self, state: StateT, version: int) -> ResponseT: ...


class DeciderVoidCommandHandler(
    DeciderCommandHandler[RequestT, None, StateT, CommandT, EventT],
    abc.ABC,
    Generic[RequestT, StateT, CommandT, EventT],
):
    @override
    def _to_response(self, state: StateT, version: int) -> None:
        return None
