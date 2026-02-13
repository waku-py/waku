from __future__ import annotations

import abc
from typing import Generic

from typing_extensions import TypeVar, override

from waku.cqrs.contracts.notification import INotification
from waku.cqrs.contracts.request import RequestT, ResponseT
from waku.cqrs.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access
from waku.cqrs.requests.handler import RequestHandler
from waku.eventsourcing.contracts.aggregate import IDecider  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.decider.repository import DeciderRepository  # noqa: TC001  # Dishka needs runtime access

__all__ = ['DeciderCommandHandler', 'DeciderVoidCommandHandler']

StateT = TypeVar('StateT', default=object)
CommandT = TypeVar('CommandT', default=object)
EventT = TypeVar('EventT', bound=INotification, default=INotification)


class DeciderCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, ResponseT, StateT, CommandT, EventT],
):
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

        if self._is_creation_command(request):
            state = self._decider.initial_state()
            version = -1
        else:
            state, version = await self._repository.load(aggregate_id)

        events = self._decider.decide(command, state)
        new_version = await self._repository.save(aggregate_id, events, version)

        for event in events:
            await self._publisher.publish(event)

        for event in events:
            state = self._decider.evolve(state, event)

        return self._to_response(state, new_version)

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    def _to_command(self, request: RequestT) -> CommandT: ...

    def _is_creation_command(self, request: RequestT) -> bool:  # noqa: ARG002, PLR6301
        return False

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
