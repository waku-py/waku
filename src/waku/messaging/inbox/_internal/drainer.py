from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.durability import IDeadLetterStore, IInboxStore
from waku.messaging.endpoints.executor import DEFERRED_TERMINAL_OUTCOMES, EndpointExecutorFactory
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.handler_map import HandlerMap
from waku.messaging.inbox._internal.finalize import apply_inbox_outcome
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import timedelta

    from dishka import AsyncContainer

    from waku.messaging._internal.identifiers import HandlerDestination
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.inbox.models import InboxEntry

__all__ = ['InboxDrainer', 'build_inbox_drainer']

logger = logging.getLogger(__name__)


class InboxPoisonError(Exception):
    """A drained row could not even reach its handler (unknown handler FQN / undeserializable payload)."""


class InboxDrainer:
    """Crash-recovery consumer for abandoned durable-inbox rows.

    Claims ``owner_id IS NULL`` INCOMING rows (FOR UPDATE SKIP LOCKED), deserializes each, resolves
    the handler from ``destination`` (FQN), executes via EndpointExecutor keyed to ``source_uri``,
    and finalizes. Executor opens its own scopes (never shares the claim tx). Pre-handler poison
    (unknown FQN / undeserializable) is bounded by ``max_attempts``: under the cap the row is left
    claimed; at the cap it is dead-lettered (if configured) and deleted.
    """

    __slots__ = (
        '_batch_size',
        '_codec',
        '_container',
        '_executor_factory',
        '_handler_by_fqn',
        '_keep_after_handled',
        '_max_attempts',
        '_owner_id',
        '_type_registry',
    )

    def __init__(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
        self,
        *,
        container: AsyncContainer,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        handler_by_fqn: Mapping[HandlerDestination, HandlerType],
        executor_factory: Callable[[str], EndpointExecutor],
        owner_id: str,
        keep_after_handled: timedelta,
        batch_size: int,
        max_attempts: int,
    ) -> None:
        self._container = container
        self._codec = codec
        self._type_registry = type_registry
        self._handler_by_fqn = handler_by_fqn
        self._executor_factory = executor_factory
        self._owner_id = owner_id
        self._keep_after_handled = keep_after_handled
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    async def drain_once(self) -> int:
        async with unit_of_work_scope(self._container) as scope:
            inbox = await scope.get(IInboxStore)
            entries = await inbox.fetch_pending_partitioned(self._batch_size, self._owner_id)
        processed = 0
        for entry in entries:
            try:
                if await self._process(entry):
                    processed += 1
            except Exception:
                logger.exception('Unhandled error draining inbox entry %s/%s', entry.id, entry.destination)
        return processed

    async def _process(self, entry: InboxEntry) -> bool:
        handler_type = self._handler_by_fqn.get(entry.destination)
        if handler_type is None:
            await self._handle_poison(entry, f'no registered handler for destination {entry.destination!r}')
            return False
        try:
            envelope = rebuild_envelope(
                entry.payload,
                wire_metadata_from_entry(entry),
                self._codec,
                self._type_registry,
            )
        except Exception as exc:  # noqa: BLE001 -- a poison row must be quarantined, not abort the batch
            await self._handle_poison(entry, f'payload rebuild failed: {exc}')
            return False
        executor = self._executor_factory(entry.source_uri)
        result = await executor.execute(envelope, handler_type)
        if result.outcome in DEFERRED_TERMINAL_OUTCOMES:
            # No live listener on the recovery path — bound like poison to prevent
            # infinite oscillation (drain → stale → drain → …).
            await self._handle_poison(entry, f'{result.outcome.value} is not enactable on the recovery path')
            return False
        await apply_inbox_outcome(
            self._container,
            entry_id=entry.id,
            destination=entry.destination,
            outcome=result.outcome,
            keep_after_handled=self._keep_after_handled,
        )
        return True

    async def _handle_poison(self, entry: InboxEntry, reason: str) -> None:
        attempt = entry.attempts + 1
        async with unit_of_work_scope(self._container) as scope:
            inbox = await scope.get(IInboxStore)
            if attempt >= self._max_attempts:
                store = await scope.get(IDeadLetterStore)
                await store.save(_poison_dead_letter(entry, reason, attempt))
                await inbox.delete(entry.id, entry.destination)
                logger.error(
                    'Dropping poison inbox row id=%s destination=%r message_type=%s after %d attempts: %s',
                    entry.id,
                    entry.destination,
                    entry.message_type,
                    attempt,
                    reason,
                )
            else:
                await inbox.increment_attempts(entry.id, entry.destination)
                logger.warning(
                    'Poison inbox row id=%s destination=%r (attempt %d/%d): %s',
                    entry.id,
                    entry.destination,
                    attempt,
                    self._max_attempts,
                    reason,
                )


async def build_inbox_drainer(container: AsyncContainer, config: InboxConfig) -> InboxDrainer:
    """Resolve app-scope collaborators and assemble the drainer (called at lifecycle start)."""
    registry = await container.get(HandlerMap)
    codec = await container.get(PayloadCodec)
    type_registry = await container.get(MessageTypeRegistry)
    factory = await container.get(EndpointExecutorFactory)

    handler_by_fqn = {handler_destination(ht): ht for ht in registry.handler_types()}

    return InboxDrainer(
        container=container,
        codec=codec,
        type_registry=type_registry,
        handler_by_fqn=handler_by_fqn,
        executor_factory=factory.for_uri,
        owner_id=config.resolve_owner_id(),
        keep_after_handled=config.keep_after_handled,
        batch_size=config.batch_size,
        max_attempts=config.max_drain_attempts,
    )


def _poison_dead_letter(entry: InboxEntry, reason: str, attempt: int) -> DeadLetterEntry:
    # Read correlation/causation from the typed columns (populated by persist/store_scheduled).
    # Fall back to message_id when the column is NULL (e.g. legacy rows written before decomposition).
    return DeadLetterEntry.from_failure(
        message_type=entry.message_type,
        payload=entry.payload,
        destination=entry.destination,
        correlation_id=entry.correlation_id if entry.correlation_id is not None else str(entry.id),
        causation_id=entry.causation_id if entry.causation_id is not None else str(entry.id),
        exc=InboxPoisonError(reason),
        attempt=attempt,
        message_id=entry.message_id,
        metadata=entry.metadata_,
        group_id=entry.group_id,
    )
