from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
from dishka import Provider, Scope, provide
from typing_extensions import override

from waku._internal.retort import default_retort  # noqa: PLC2701
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, IDeadLetterStore
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.relay import OutboxRelayConfig, build_relay_default_policy
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicyRegistry
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.messaging.transport.registry import TransportRegistry
from waku.serialization.codec import PayloadCodec
from waku.serialization.upcasting import UpcasterChain
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from waku.messaging.contracts.message import IMessage
    from waku.messaging.sending import SendingFailurePolicy
    from waku.messaging.transport.inbound import ConsumeCallback


def make_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


def make_envelope(
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    group_id: str | None = None,
    scheduled_time: datetime | None = None,
    expires_at: datetime | None = None,
) -> MessageEnvelope[Any]:
    payload_type = type(payload)
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_type=f'{payload_type.__module__}.{payload_type.__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=payload,
        headers=headers or {},
        group_id=group_id,
        scheduled_time=scheduled_time,
        expires_at=expires_at,
    )


def make_relay_evaluator(
    config: OutboxRelayConfig,
    *,
    destination_policies: Mapping[str, Sequence[SendingFailurePolicy]] | None = None,
) -> SendingFailureEvaluator:
    """Build the relay's `SendingFailureEvaluator` with the synthesized default + optional per-destination policies."""
    registry = SendingFailurePolicyRegistry(
        destination_policies=destination_policies or {},
        default_policies=(build_relay_default_policy(config),),
    )
    return SendingFailureEvaluator(registry=registry)


NOOP_EVALUATOR = ErrorPolicyEvaluator(registry=ErrorPolicyRegistry(handler_policies={}, default_policies=()))


class FakeUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.committed = False
        self.rolled_back = False
        self.commit_count = 0
        self.rollback_count = 0
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    @override
    async def commit(self) -> None:
        if self._commit_error:
            raise self._commit_error
        self.committed = True
        self.commit_count += 1

    @override
    async def rollback(self) -> None:
        if self._rollback_error:
            raise self._rollback_error
        self.rolled_back = True
        self.rollback_count += 1


class RecordingDeadLetterStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.entries: list[DeadLetterEntry] = []

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.entries.append(entry)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:  # pragma: no cover
        raise KeyError(entry_id)

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def claim_replayable(
        self,
        batch_size: int,
        max_replay_count: int,
    ) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:  # pragma: no cover
        pass

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0


class FailingDeadLetterStore(IDeadLetterStore):
    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        msg = 'DLQ store unavailable'
        raise ConnectionError(msg)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:  # pragma: no cover
        raise KeyError(entry_id)

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def claim_replayable(
        self,
        batch_size: int,
        max_replay_count: int,
    ) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:  # pragma: no cover
        pass

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0


class StubSubscription(Subscription):
    """No-op Subscription double for fake transports whose ``subscribe`` is never driven."""

    @override
    async def pause(self) -> None: ...

    @override
    async def resume(self) -> None: ...


class RecordingTransport(ITransport):
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], str, EnvelopeMetadata, IEnvelopeMapper[Any, Any] | None]] = []
        self.sent_event = anyio.Event()
        self.subscribed: list[tuple[str, ConsumeCallback, IEnvelopeMapper[Any, Any] | None]] = []

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        self.sent.append((body, destination, metadata, mapper))
        self.sent_event.set()

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        self.subscribed.append((queue, on_message, mapper))
        return StubSubscription()

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


class RecordingAllocator(ISequenceAllocator):
    """ISequenceAllocator double: records group_ids in ``calls`` and returns per-group sequences."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._counters: dict[str, int] = {}

    @override
    async def allocate(self, group_id: str) -> int:
        self.calls.append(group_id)
        self._counters[group_id] = self._counters.get(group_id, 0) + 1
        return self._counters[group_id]


def order_id_partition(msg: IMessage) -> str | None:
    """partition_by extractor for test messages carrying an ``order_id`` attribute."""
    order_id: str = msg.order_id  # type: ignore[attr-defined]
    return order_id


class RelayDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: IOutboxStore,
        transport: ITransport,
        external_mappers: Mapping[str, IEnvelopeMapper[Any, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._registry = TransportRegistry({'test': transport}, external_mappers=external_mappers)
        self._uow: IUnitOfWork = FakeUoW()

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._store

    @provide(scope=Scope.APP)
    def transport_registry(self) -> TransportRegistry:
        return self._registry

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow
