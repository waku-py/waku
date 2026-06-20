from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from waku.messaging._identifiers import EndpointUri
from waku.messaging.inbox._destination import handler_destination
from waku.messaging.inbox.finalize import apply_inbox_outcome
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.partition import resolve_and_allocate
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging._identifiers import HandlerDestination
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.partition import PartitionKeyExtractor

__all__ = [
    'DurableReceiver',
]

logger = logging.getLogger(__name__)


class DurableReceiver:
    """Wraps handler execution with inbox write-ahead persistence for external-transport listeners.

    Flow: store_incoming + commit → executor.execute → mark_handled | delete.

    IDEMPOTENCY CONTRACT. Waku's scope model uses THREE distinct transactions: inbox write (scope A),
    handler business writes (scope B, owned by TransactionalBehavior), mark_as_handled (scope C).
    A crash after B commits but before C commits leaves a still-INCOMING row;
    InboxRecoveryWorker re-dispatches, re-running the handler and duplicating its side-effects.
    Handlers MUST be idempotent at the business level (UPSERT, causation_id dedup, etc.).
    """

    __slots__ = ('_container', '_endpoint_uri', '_executor', '_inbox_config', '_owner_id', '_partition_by')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        executor: EndpointExecutor,
        inbox_config: InboxConfig,
        owner_id: str,
        endpoint_uri: str,
        partition_by: PartitionKeyExtractor | None = None,
    ) -> None:
        self._container = container
        self._executor = executor
        self._inbox_config = inbox_config
        self._owner_id = owner_id
        self._endpoint_uri = endpoint_uri
        self._partition_by = partition_by

    async def receive(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        # `destination` = handler FQN: same (message, handler) deduplicates; same message, different handler proceeds.
        destination = handler_destination(handler_type)
        stored = await self._persist(envelope, destination)
        if not stored:
            logger.debug(
                'Duplicate message discarded: message_id=%s destination=%s',
                envelope.message_id,
                destination,
            )
            return

        # External-listener path: REQUEUE/PAUSE re-delivery is a local-queue concern, unavailable here.
        result = await self._executor.execute(envelope, handler_type)
        await apply_inbox_outcome(
            self._container,
            entry_id=envelope.message_id,
            destination=destination,
            outcome=result.outcome,
            keep_after_handled=self._inbox_config.keep_after_handled,
        )

    async def _persist(self, envelope: MessageEnvelope[Any], destination: HandlerDestination) -> bool:
        async with self._container() as scope:
            inbox = await scope.get(IInboxStore)
            serializer = await scope.get(IEnvelopeSerializer)
            uow = await scope.get(IUnitOfWork)
            group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, scope)
            entry = InboxEntry(
                id=envelope.message_id,
                payload=serializer.serialize(envelope),
                message_type=envelope.message_type,
                source_uri=EndpointUri(self._endpoint_uri),
                destination=destination,
                owner_id=self._owner_id,
                group_id=group_id,
                sequence_number=sequence_number,
            )
            stored: bool = await inbox.store_incoming(entry)
            await uow.commit()
            return stored
