from __future__ import annotations

import abc
import logging
from typing import Generic

from typing_extensions import override

from waku.eventsourcing.contracts.aggregate import (
    CommandT,
    EventT,
    IDecider,  # Dishka needs runtime access
    StateT,
)
from waku.eventsourcing.decider.repository import DeciderRepository  # noqa: TC001  # Dishka needs runtime access
from waku.integrations.eventsourcing_messaging._internal.command_handler import OptimisticRetryCommandHandler
from waku.integrations.eventsourcing_messaging._internal.retry import execute_with_optimistic_retry
from waku.messaging.contracts.message import ResponseT
from waku.messaging.contracts.request import RequestT

__all__ = ['DeciderCommandHandler', 'DeciderVoidCommandHandler']

logger = logging.getLogger(__name__)


class DeciderCommandHandler(
    OptimisticRetryCommandHandler[RequestT, ResponseT],
    abc.ABC,
    Generic[RequestT, StateT, CommandT, EventT, ResponseT],
):
    def __init__(
        self,
        repository: DeciderRepository[StateT, CommandT, EventT],
        decider: IDecider[StateT, CommandT, EventT],
    ) -> None:
        self._repository = repository
        self._decider = decider

    @override
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

            # Appended events are forwarded to the outbox by EventForwardingBehavior (the store records
            # them into the scoped collector during save) — no in-handler publish (that was the torn-write).
            new_version: int = await self._repository.save(
                aggregate_id,
                events,
                version,
                current_state=state,
                idempotency_key=idempotency_key,
            )

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
