from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING

from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging._internal.identity import MessageTypeRegistry  # noqa: TC001
from waku.messaging.errors.dead_letter import IDeadLetterStore  # noqa: TC001
from waku.messaging.router import MessageRouter  # noqa: TC001
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec  # noqa: TC001

if TYPE_CHECKING:
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = [
    'ReplayExecutor',
]

logger = logging.getLogger(__name__)


class ReplayExecutor:
    """Re-injects a dead-lettered message back into the normal pipeline.

    Reconstructs the envelope via ``rebuild_envelope`` from the entry's columns + metadata; the rebuilt
    ``message_id`` is the original envelope ``message_id`` stored in the ``message_id`` column, so it is
    stable and correct across re-replays of the same entry. Looks up ``entry.destination`` via the router,
    re-dispatches, and marks the outcome. At-least-once: replay re-enters the pipeline, so idempotency leans
    on the inbox ``(message_id, destination)`` dedup. NEVER commits — the caller owns the transaction boundary.
    """

    __slots__ = ('_codec', '_container', '_router', '_store', '_type_registry')

    def __init__(
        self,
        container: AsyncContainer,
        store: IDeadLetterStore,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        router: MessageRouter,
    ) -> None:
        self._container = container
        self._store = store
        self._codec = codec
        self._type_registry = type_registry
        self._router = router

    async def replay(self, entry: DeadLetterEntry) -> bool:
        endpoint = self._router.endpoint_for(entry.destination)
        if endpoint is None:
            await self._store.mark_replay_failed(
                entry.id,
                f'no endpoint registered for destination {entry.destination!r}',
            )
            return False
        try:
            envelope = rebuild_envelope(
                entry.payload,
                wire_metadata_from_entry(entry),
                self._codec,
                self._type_registry,
            )
            await endpoint.dispatch(envelope, self._container)
        except Exception as exc:  # noqa: BLE001 -- record any re-injection failure, never raise to the worker
            await self._store.mark_replay_failed(entry.id, ''.join(traceback.format_exception(exc)))
            logger.warning('Replay failed for dead letter %s: %s', entry.id, exc)
            return False
        await self._store.mark_replayed(entry.id)
        return True

    async def replay_by_id(self, entry_id: UUID) -> bool:
        entry = await self._store.fetch_one(entry_id)
        return await self.replay(entry)
