from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anyio
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.identifiers import EndpointUri
from waku.messaging._internal.partition import resolve_group_id
from waku.messaging.endpoints._internal.durable_inbox_receiver import build_durable_inbox_receiver
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.inbox._internal.destination import handler_destination
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.observability.observer import MessageObservers
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.router import HandlerSubscriptions

logger = logging.getLogger(__name__)

__all__ = [
    'DurableLocalQueueEndpoint',
]


class DurableLocalQueueEndpoint(Endpoint):
    """Inbox-backed local queue with crash recovery.

    Commits rows BEFORE enqueueing — a crash leaves INCOMING rows for recovery.
    Cross-pod dedup via ``(message_id, handler_FQN)``; concurrency via SKIP LOCKED.
    Delegates lifecycle to ``DurableInboxReceiver``.
    """

    __slots__ = ('_container', '_handler_subscriptions', '_now', '_observers', '_partition_by', '_receiver')

    def __init__(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: EndpointExecutor,
        observers: MessageObservers,
        container: AsyncContainer,
        inbox_config_keep_after_handled_seconds: float,
        inbox_owner_id: str,
        stop_timeout: float,
        max_buffer_size: float,
        partition_by: PartitionKeyExtractor | None = None,
        max_requeue_attempts: int = 5,
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        now: Now = utc_now,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._observers = observers
        self._container = container
        self._partition_by = partition_by
        self._now = now
        self._receiver = build_durable_inbox_receiver(
            uri=uri,
            container=container,
            executor=executor,
            inbox_owner_id=inbox_owner_id,
            keep_after_handled=timedelta(seconds=inbox_config_keep_after_handled_seconds),
            partition_by=partition_by,
            max_requeue_attempts=max_requeue_attempts,
            pause_sleep=pause_sleep,
            circuit_breaker_config=circuit_breaker_config,
            max_buffer_size=max_buffer_size,
            stop_timeout=stop_timeout,
        )

    @property
    @override
    def supports_scheduling(self) -> bool:
        return True  # durable inbox survives restarts; promotion runs the message when due

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Persist one inbox row per handler in a dedicated scope (not the caller's), then enqueue.

        Caller's ``scope`` is unused: reusing it would commit a business transaction prematurely in cascading-send.
        """
        if not self._receiver.is_running:
            logger.warning('Message dropped: endpoint %s is stopped (message_id=%s)', self._uri, envelope.message_id)
            return

        handler_types = self._handler_subscriptions.get(type(envelope.payload), frozenset())
        if not handler_types:
            return

        scheduled = envelope.scheduled_time
        if scheduled is not None and scheduled > self._now():
            # SCHEDULED rows: no sequence allocated, no enqueue — memory stream can't survive a restart.
            # Promotion allocates the sequence at due-time so delayed messages sort after already-queued siblings (BLOCKER 1).
            await self._store_scheduled(envelope, handler_types, scheduled)
            await self._observers.sent(envelope, self._uri)
            return

        fresh = await self._receiver.persist(envelope, handler_types)

        if not fresh:
            logger.debug('Duplicate message discarded for all handlers: message_id=%s', envelope.message_id)
            return

        await self._receiver.enqueue(envelope, fresh)
        await self._observers.sent(envelope, self._uri)

    async def _store_scheduled(
        self,
        envelope: MessageEnvelope[Any],
        handler_types: frozenset[HandlerType],
        scheduled: datetime,
    ) -> None:
        group_id = resolve_group_id(envelope, self._partition_by)  # partition resolved; sequence deferred
        async with unit_of_work_scope(self._container) as write_scope:
            inbox = await write_scope.get(IInboxStore)
            codec = await write_scope.get(PayloadCodec)
            payload = encode_payload(envelope, codec)
            metadata_ = encode_metadata(envelope)
            for handler_type in handler_types:
                # owner_id=None so the recovery drain (owner_id IS NULL) claims the promoted row.
                await inbox.store_incoming(
                    InboxEntry(
                        id=envelope.message_id,
                        payload=payload,
                        message_type=envelope.message_type,
                        source_uri=EndpointUri(self._uri),
                        destination=handler_destination(handler_type),
                        group_id=group_id,
                        sequence_number=None,
                        owner_id=None,
                        status=InboxStatus.SCHEDULED,
                        execution_time=scheduled,
                        correlation_id=envelope.correlation_id,
                        causation_id=envelope.causation_id,
                        metadata_=metadata_,
                    )
                )

    @override
    async def start(self) -> None:
        await self._receiver.start()

    @override
    async def stop(self) -> None:
        await self._receiver.stop()

    @override
    async def pause(self) -> PauseToken:
        return await self._receiver.pause()

    @override
    async def resume(self, token: PauseToken | None = None) -> None:
        await self._receiver.resume(token)
