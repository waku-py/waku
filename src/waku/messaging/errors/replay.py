from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING

from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging._internal.dispatcher import MessageDispatcher  # noqa: TC001
from waku.messaging._internal.identity import MessageTypeRegistry  # noqa: TC001
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

__all__ = [
    'ReplayExecutor',
]

logger = logging.getLogger(__name__)


class ReplayExecutor:
    """Re-injects a dead-lettered message back into the normal pipeline.

    Reconstructs the envelope via ``rebuild_envelope`` from the entry's columns + metadata; the rebuilt
    ``message_id`` is the original envelope ``message_id`` stored in the ``message_id`` column, so it is
    stable and correct across re-replays of the same entry. Resolution branches on
    ``entry.destination_kind``:

    - ``ENDPOINT``: ``destination`` is an endpoint URI — resolved via the router and re-dispatched.
    - ``HANDLER``: ``destination`` is a handler FQN (inbox-poison origin) — the ONE resolved handler
      is reprocessed inline through the dispatcher (full pipeline, NO error policies: a re-failure
      propagates here and marks ``REPLAY_FAILED`` instead of re-dead-lettering). The reprocess runs
      in a FRESH request scope so the handler's own transaction stays separate from the worker's
      claim transaction; ``mark_replayed``/``mark_replay_failed`` stay on the worker-scoped store.

    An unresolvable destination marks ``REPLAY_FAILED``. At-least-once: replay re-enters the pipeline,
    so idempotency leans on the inbox ``(message_id, destination)`` dedup. NEVER commits — the caller
    owns the transaction boundary.
    """

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
        # Drainer parity: the same FQN mapping the inbox writes as `destination`.
        self._handler_by_fqn: dict[str, HandlerType] = {
            handler_destination(ht): ht for ht in handler_map.handler_types()
        }

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
        except Exception as exc:  # noqa: BLE001 -- record any re-injection failure, never raise to the worker
            await self._store.mark_replay_failed(entry.id, ''.join(traceback.format_exception(exc)))
            logger.warning('Replay failed for dead letter %s: %s', entry.id, exc)
            return False
        await self._store.mark_replayed(entry.id)
        return True

    async def _reprocess(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        # Mirrors the live durable path (`EndpointExecutor._dispatch_in_scope`): fresh scope per
        # attempt + message context; the handler's TransactionalBehavior commits its own scope's UoW.
        async with self._scopes.fresh_scope() as scope:
            with message_context_scope(envelope):
                await self._dispatcher.dispatch_to_handler(scope, envelope, handler_type)
