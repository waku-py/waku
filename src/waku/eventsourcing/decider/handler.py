from __future__ import annotations

import abc
import logging
from typing import ClassVar, Generic

from typing_extensions import TypeVar, override

from waku.cqrs.contracts.notification import INotification
from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access
from waku.cqrs.requests.handler import RequestHandler
from waku.eventsourcing._retry import execute_with_optimistic_retry
from waku.eventsourcing.contracts.aggregate import IDecider  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.decider.repository import DeciderRepository  # noqa: TC001  # Dishka needs runtime access

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

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if 'max_attempts' in cls.__dict__ and cls.max_attempts < 1:
            msg = f'{cls.__name__}.max_attempts must be >= 1, got {cls.max_attempts}'
            raise ValueError(msg)

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
        aggregate_id: str = self._aggregate_id(request)
        command: CommandT = self._to_command(request)
        idempotency_key: str | None = self._idempotency_key(request)
        logger.debug('Handling %s for %s', type(request).__name__, aggregate_id)

        async def _attempt() -> ResponseT:
            state, version = await self._repository.load(aggregate_id)

            events = self._decider.decide(command, state)
            for event in events:
                state = self._decider.evolve(state, event)

            new_version: int = await self._repository.save(
                aggregate_id,
                events,
                version,
                current_state=state,
                idempotency_key=idempotency_key,
            )

            for event in events:
                await self._publisher.publish(event)

            return self._to_response(state, new_version)

        return await execute_with_optimistic_retry(
            _attempt,
            max_attempts=self.max_attempts,
            request_name=type(request).__name__,
            aggregate_id=aggregate_id,
        )

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    def _to_command(self, request: RequestT) -> CommandT: ...

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
