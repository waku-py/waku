from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dishka import Provider, Scope, provide
from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.outbox.interfaces import IOutboxStore  # noqa: TC001
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer, JsonEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


def make_serializer(*types: type) -> JsonEnvelopeSerializer:
    registry = {f'{t.__module__}.{t.__qualname__}': t for t in types}
    return JsonEnvelopeSerializer(type_registry=registry)


def make_envelope(payload: Any, *, headers: dict[str, str] | None = None) -> MessageEnvelope[Any]:
    payload_type = type(payload)
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_type=f'{payload_type.__module__}.{payload_type.__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=payload,
        headers=headers or {},
    )


NOOP_EVALUATOR = ErrorPolicyEvaluator(registry=ErrorPolicyRegistry(()))


class FakeUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.committed = False
        self.rolled_back = False
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    @override
    async def commit(self) -> None:
        if self._commit_error:
            raise self._commit_error
        self.committed = True

    @override
    async def rollback(self) -> None:
        if self._rollback_error:
            raise self._rollback_error
        self.rolled_back = True


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
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0


class RecordingTransport(ITransport):
    def __init__(self) -> None:
        self.sent: list[tuple[MessageEnvelope[Any], str]] = []

    @override
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None:
        self.sent.append((envelope, destination))


class RelayDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: IOutboxStore,
        transport: ITransport,
        serializer: IEnvelopeSerializer,
    ) -> None:
        super().__init__()
        self._store = store
        self._transport = transport
        self._serializer = serializer
        self._uow: IUnitOfWork = FakeUoW()

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._store

    @provide(scope=Scope.APP)
    def transport(self) -> ITransport:
        return self._transport

    @provide
    def serializer(self) -> IEnvelopeSerializer:
        return self._serializer

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow
