from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import anyio
from typing_extensions import override

from waku._internal.transaction import TransactionCleanupError
from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging._internal.dispatcher import MessageDispatcher  # noqa: TC001
from waku.messaging._internal.identity import MessageTypeRegistry  # noqa: TC001
from waku.messaging._internal.transaction import CompletedExecutionError
from waku.messaging.context import message_context_scope
from waku.messaging.durability import IDeadLetterStore  # noqa: TC001
from waku.messaging.errors._internal.reprocess import ReprocessScopeOpener  # noqa: TC001
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind
from waku.messaging.handler_map import HandlerMap  # noqa: TC001
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.router import MessageRouter  # noqa: TC001
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any
    from uuid import UUID

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.dead_letter import DeadLetterEntry

logger = logging.getLogger(__name__)


class IReplayExecution(ABC):
    @abstractmethod
    async def replay(self, entry: DeadLetterEntry) -> bool: ...


class ReplayExecution(IReplayExecution):
    """Signal-preserving dead-letter replay used by the maintenance transaction owner."""

    __slots__ = (
        '_codec',
        '_container',
        '_dispatcher',
        '_handler_by_fqn',
        '_router',
        '_scopes',
        '_store',
        '_type_registry',
    )

    def __init__(  # noqa: PLR0913 -- DI collaborators, all required; dishka-injected
        self,
        *,
        container: AsyncContainer,
        store: IDeadLetterStore,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        router: MessageRouter,
        dispatcher: MessageDispatcher,
        handler_map: HandlerMap,
        scopes: ReprocessScopeOpener,
    ) -> None:
        self._container = container
        self._store = store
        self._codec = codec
        self._type_registry = type_registry
        self._router = router
        self._dispatcher = dispatcher
        self._scopes = scopes
        self._handler_by_fqn: dict[str, HandlerType] = {
            handler_destination(handler_type): handler_type for handler_type in handler_map.handler_types()
        }

    @override
    async def replay(self, entry: DeadLetterEntry) -> bool:
        if entry.destination_kind is DeadLetterDestinationKind.HANDLER:
            return await self._replay_to_handler(entry)
        return await self._replay_to_endpoint(entry)

    async def replay_by_id(self, entry_id: UUID) -> bool:
        entry = await self._store.fetch_one(entry_id)
        return await self.replay(entry)

    async def _replay_to_endpoint(self, entry: DeadLetterEntry) -> bool:
        endpoint = self._router.endpoint_for(entry.destination)
        if endpoint is None:
            await self._store.mark_replay_failed(
                entry.id,
                f'no endpoint registered for destination {entry.destination!r}',
            )
            return False
        return await self._attempt(entry, lambda envelope: endpoint.dispatch(envelope, self._container))

    async def _replay_to_handler(self, entry: DeadLetterEntry) -> bool:
        handler_type = self._handler_by_fqn.get(entry.destination)
        if handler_type is None:
            await self._store.mark_replay_failed(
                entry.id,
                f'no registered handler for destination {entry.destination!r}',
            )
            return False
        return await self._attempt(entry, lambda envelope: self._reprocess(envelope, handler_type))

    async def _attempt(
        self,
        entry: DeadLetterEntry,
        dispatch: Callable[[MessageEnvelope[Any]], Awaitable[None]],
    ) -> bool:
        try:
            envelope = rebuild_envelope(
                entry.payload,
                wire_metadata_from_entry(entry),
                self._codec,
                self._type_registry,
            )
            await dispatch(envelope)
        except CompletedExecutionError as completed_error:
            await self._mark_replayed_after_dispatch(entry.id, completed_error=completed_error)
            raise
        except TransactionCleanupError:
            raise
        except Exception as exc:  # noqa: BLE001 -- record any safely-aborted re-injection failure
            await self._store.mark_replay_failed(entry.id, ''.join(traceback.format_exception(exc)))
            logger.warning('Replay failed for dead letter %s: %s', entry.id, exc)
            return False
        await self._mark_replayed_after_dispatch(entry.id)
        return True

    async def _mark_replayed_after_dispatch(
        self,
        entry_id: UUID,
        *,
        completed_error: CompletedExecutionError | None = None,
    ) -> None:
        try:
            if completed_error is None:
                await self._store.mark_replayed(entry_id)
            else:
                with anyio.CancelScope(shield=True):
                    await self._store.mark_replayed(entry_id)
        except Exception as finalization_error:  # noqa: BLE001 -- any failed post-dispatch mark must stay fatal
            raise CompletedExecutionError(finalization_error) from completed_error
        except anyio.get_cancelled_exc_class() as finalization_error:
            raise CompletedExecutionError(finalization_error) from completed_error

    async def _reprocess(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        execution_completed = False
        try:
            async with self._scopes.fresh_scope() as scope:
                with message_context_scope(envelope):
                    await self._dispatcher.dispatch_to_handler(scope, envelope, handler_type)
                execution_completed = True
        except BaseException as error:
            if execution_completed:
                raise CompletedExecutionError(error) from error
            raise
