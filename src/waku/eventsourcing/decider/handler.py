from __future__ import annotations

import abc
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from typing_extensions import override

from waku.eventsourcing._retry import execute_with_optimistic_retry
from waku.eventsourcing.contracts.aggregate import (
    CommandT,
    EventT,
    IDecider,  # Dishka needs runtime access
    StateT,
)
from waku.eventsourcing.decider.repository import DeciderRepository  # noqa: TC001  # Dishka needs runtime access
from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT
from waku.messaging.handler import RequestHandler
from waku.messaging.interfaces import IPublisher  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

__all__ = ['DeciderCommandHandler', 'DeciderVoidCommandHandler']

logger = logging.getLogger(__name__)


class DeciderCommandHandler(
    RequestHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, StateT, CommandT, EventT, ResponseT],
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
        logger.debug('Handling %s for %s', type(request).__name__, aggregate_id)

        async def _attempt() -> ResponseT:
            state, version = await self._repository.load(aggregate_id)
            idempotency_key = self._idempotency_key(request, version)

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
            attempt_context=self._create_attempt_context,
        )

    @abc.abstractmethod
    def _aggregate_id(self, request: RequestT) -> str: ...

    @abc.abstractmethod
    def _to_command(self, request: RequestT) -> CommandT: ...

    def _idempotency_key(self, request: RequestT, version: int) -> str | None:  # noqa: ARG002, PLR6301
        """Return a deduplication token for idempotent event appends.

        Args:
            request: The incoming command request.
            version: Stream version at load time (``-1`` for new streams).
        """
        return None

    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:  # noqa: PLR6301
        """Return a new context manager for a single retry attempt.

        Called once per attempt — must return a fresh instance each time.
        """
        return nullcontext()

    @abc.abstractmethod
    def _to_response(self, state: StateT, version: int) -> ResponseT: ...


class DeciderVoidCommandHandler(
    DeciderCommandHandler[RequestT, StateT, CommandT, EventT, None],
    abc.ABC,
    Generic[RequestT, StateT, CommandT, EventT],
):
    @override
    def _to_response(self, state: StateT, version: int) -> None:
        return None
