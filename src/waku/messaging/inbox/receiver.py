from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, assert_never

from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.inbox._destination import handler_destination
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.inbox.config import InboxConfig

__all__ = [
    'DurableReceiver',
]

logger = logging.getLogger(__name__)


class DurableReceiver:
    """Wraps handler execution with inbox write-ahead persistence.

    Flow: store_incoming + commit -> executor.execute -> mark_handled | delete.

    M2d wires DurableReceiver into FastStream (and other external transport) listeners. M2b.1
    ships the class + DI-ready constructor; until the M2d listener-side adapter lands, external
    listeners process messages directly through EndpointExecutor without inbox persistence.

    IDEMPOTENCY CONTRACT (at-least-once delivery semantics).

    Waku's scope model forbids sharing a UoW across scopes, so the inbox write (scope A), the
    handler's business writes (scope B, owned by TransactionalBehavior inside EndpointExecutor),
    and the mark_as_handled (scope C) are THREE distinct transactions. This is architecturally
    different from Wolverine, which atomically commits inbox + business state via transactional
    middleware.

    Concrete consequence: if the process crashes AFTER scope B commits (handler wrote business
    data) but BEFORE scope C commits (inbox marked HANDLED), InboxRecoveryWorker.recover_stale
    will later reclaim the still-INCOMING entry and re-dispatch to the handler — the handler
    re-runs and duplicates its side-effects.

    Handlers downstream of a durable inbox MUST be idempotent at the business level (check-before-write
    on unique keys, UPSERT, idempotency columns, causation_id dedup). The framework does NOT provide
    at-most-once semantics for handler side-effects.
    """

    __slots__ = ('_container', '_endpoint_uri', '_executor', '_inbox_config', '_owner_id')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        executor: EndpointExecutor,
        inbox_config: InboxConfig,
        owner_id: str,
        endpoint_uri: str,
    ) -> None:
        self._container = container
        self._executor = executor
        self._inbox_config = inbox_config
        self._owner_id = owner_id
        self._endpoint_uri = endpoint_uri

    async def receive(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        # `destination` is the handler FQN — the per-handler dedup discriminator. A redelivery of
        # the same message to the same handler dedups; the same message to a different handler proceeds.
        destination = handler_destination(handler_type)
        stored = await self._persist(envelope, destination)
        if not stored:
            logger.debug(
                'Duplicate message discarded: message_id=%s destination=%s',
                envelope.message_id,
                destination,
            )
            return

        outcome = await self._executor.execute(envelope, handler_type)
        await self._apply_outcome(envelope, destination, outcome)

    async def _persist(self, envelope: MessageEnvelope[Any], destination: str) -> bool:
        async with self._container() as scope:
            inbox = await scope.get(IInboxStore)
            serializer = await scope.get(IEnvelopeSerializer)
            uow = await scope.get(IUnitOfWork)
            entry = InboxEntry(
                id=envelope.message_id,
                payload=serializer.serialize(envelope),
                message_type=envelope.message_type,
                received_at=self._endpoint_uri,
                destination=destination,
                owner_id=self._owner_id,
            )
            stored: bool = await inbox.store_incoming(entry)
            await uow.commit()
            return stored

    async def _apply_outcome(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        outcome: ExecutionOutcome,
    ) -> None:
        match outcome:
            case ExecutionOutcome.SUCCESS:
                await self._mark_handled(envelope, destination)
            case ExecutionOutcome.DEAD_LETTERED | ExecutionOutcome.DISCARDED | ExecutionOutcome.FAILED_NO_POLICY:
                await self._delete(envelope, destination)
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    async def _mark_handled(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        keep_until = datetime.now(tz=UTC) + self._inbox_config.keep_after_handled
        async with self._container() as scope:
            inbox = await scope.get(IInboxStore)
            uow = await scope.get(IUnitOfWork)
            await inbox.mark_as_handled(envelope.message_id, destination, keep_until)
            await uow.commit()

    async def _delete(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        # EndpointExecutor already wrote to IDeadLetterStore (on DEAD_LETTERED) or logged the
        # discard. Remove this handler's inbox row directly — do NOT reuse mark_as_handled for
        # non-success outcomes, because that pollutes observability (a DLQ'd message would read
        # as HANDLED in the inbox).
        async with self._container() as scope:
            inbox = await scope.get(IInboxStore)
            uow = await scope.get(IUnitOfWork)
            await inbox.delete(envelope.message_id, destination)
            await uow.commit()
