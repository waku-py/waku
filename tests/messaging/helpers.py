from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterWriter
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.transport.serialization import JsonEnvelopeSerializer
from waku.uow import IUnitOfWork


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


def make_dead_letter_entry(**overrides: Any) -> DeadLetterEntry:
    defaults: dict[str, Any] = {
        'id': uuid4(),
        'message_type': 'test.module.SomeEvent',
        'payload': {'key': 'value'},
        'destination': 'test://q',
        'correlation_id': uuid4(),
        'causation_id': uuid4(),
        'error_type': 'builtins.ValueError',
        'error_message': 'bad input',
        'retry_count': 3,
    }
    defaults.update(overrides)
    return DeadLetterEntry(**defaults)


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


class RecordingDeadLetterWriter(IDeadLetterWriter):
    def __init__(self) -> None:
        self.entries: list[DeadLetterEntry] = []

    @override
    async def write(self, entry: DeadLetterEntry) -> None:
        self.entries.append(entry)


class FailingDeadLetterWriter(IDeadLetterWriter):
    @override
    async def write(self, entry: DeadLetterEntry) -> None:
        msg = 'DLQ store unavailable'
        raise ConnectionError(msg)
