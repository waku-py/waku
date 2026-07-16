from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from waku._internal.transaction import TransactionCleanupError, unit_of_work_scope
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging._internal.transaction import CompletedExecutionError
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.execution import (
    EndpointExecutionFactory,
    ExecutionResult,
    IEndpointExecution,
    TerminalIntent,
    TerminalIntentKind,
)
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.handler_map import HandlerMap
from waku.messaging.inbox._internal.finalize import apply_inbox_outcome
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import timedelta

    from dishka import AsyncContainer

    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.inbox.identifiers import HandlerDestination
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
        executor_factory: Callable[[str], IEndpointExecution],
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
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            inbox = await scope.get(IInboxStore)
            entries = await inbox.fetch_pending_partitioned(self._batch_size, self._owner_id)
        processed = 0
        for entry in entries:
            try:
                if await self._process(entry):
                    processed += 1
            except (CompletedExecutionError, TransactionCleanupError):
                raise
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
        intent = await executor.execute(envelope, handler_type)
        if intent.kind in {TerminalIntentKind.REQUEUE, TerminalIntentKind.PAUSE}:
            # No live listener on the recovery path — bound like poison to prevent
            # infinite oscillation (drain → stale → drain → …).
            reason = f'{intent.kind.value} is not enactable on the recovery path'
            poison_intent = replace(
                intent,
                kind=TerminalIntentKind.DEAD_LETTER,
                error=InboxPoisonError(reason),
                attempt=entry.attempts + 1,
            )
            result = await self._handle_poison(entry, reason, poison_intent)
            if result is None:
                return False
            await executor.emit_terminal(envelope, handler_type, poison_intent, result)
            return True
        dead_letter = _dead_letter_for_intent(entry, intent)
        result = await apply_inbox_outcome(
            self._container,
            entry_id=entry.id,
            destination=entry.destination,
            intent=intent,
            keep_after_handled=self._keep_after_handled,
            dead_letter=dead_letter,
        )
        await executor.emit_terminal(envelope, handler_type, intent, result)
        return True

    async def _handle_poison(
        self,
        entry: InboxEntry,
        reason: str,
        intent: TerminalIntent | None = None,
    ) -> ExecutionResult | None:
        attempt = entry.attempts + 1
        if attempt >= self._max_attempts:
            poison_intent = intent or TerminalIntent(
                TerminalIntentKind.DEAD_LETTER,
                error=InboxPoisonError(reason),
                attempt=attempt,
            )
            result = await apply_inbox_outcome(
                self._container,
                entry_id=entry.id,
                destination=entry.destination,
                intent=poison_intent,
                keep_after_handled=self._keep_after_handled,
                dead_letter=_poison_dead_letter(entry, reason, attempt),
            )
            logger.error(
                'Dropping poison inbox row id=%s destination=%r message_type=%s after %d attempts: %s',
                entry.id,
                entry.destination,
                entry.message_type,
                attempt,
                reason,
            )
            return result
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            inbox = await scope.get(IInboxStore)
            await inbox.increment_attempts(entry.id, entry.destination)
            logger.warning(
                'Poison inbox row id=%s destination=%r (attempt %d/%d): %s',
                entry.id,
                entry.destination,
                attempt,
                self._max_attempts,
                reason,
            )
        return None


async def build_inbox_drainer(container: AsyncContainer, config: InboxConfig) -> InboxDrainer:
    """Resolve app-scope collaborators and assemble the drainer (called at lifecycle start)."""
    registry = await container.get(HandlerMap)
    codec = await container.get(PayloadCodec)
    type_registry = await container.get(MessageTypeRegistry)
    factory = await container.get(EndpointExecutionFactory)

    handler_by_fqn = {handler_destination(handler_type): handler_type for handler_type in registry.handler_types()}

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
    # correlation/causation come from the typed columns (populated by persist/store_scheduled).
    return DeadLetterEntry.from_failure(
        message_type=entry.message_type,
        payload=entry.payload,
        destination=entry.destination,
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id=entry.correlation_id,
        causation_id=entry.causation_id,
        exc=InboxPoisonError(reason),
        attempt=attempt,
        message_id=entry.message_id,
        metadata=entry.metadata,
        group_id=entry.group_id,
    )


def _dead_letter_for_intent(
    entry: InboxEntry,
    intent: TerminalIntent,
) -> DeadLetterEntry | None:
    if intent.kind is not TerminalIntentKind.DEAD_LETTER:
        return None
    if intent.error is None:
        msg = 'dead-letter intent must retain its handler failure'
        raise RuntimeError(msg)
    return DeadLetterEntry.from_failure(
        message_type=entry.message_type,
        payload=entry.payload,
        destination=entry.destination,
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id=entry.correlation_id,
        causation_id=entry.causation_id,
        exc=intent.error,
        attempt=intent.attempt,
        message_id=entry.message_id,
        metadata=entry.metadata,
        group_id=entry.group_id,
    )
