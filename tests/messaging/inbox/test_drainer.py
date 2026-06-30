from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging._identifiers import EndpointUri, HandlerDestination  # noqa: PLC2701
from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome, ExecutionResult
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.inbox._destination import handler_destination  # noqa: PLC2701
from waku.messaging.inbox.drainer import InboxDrainer
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.transport.decomposition import encode_metadata, encode_payload
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingDeadLetterStore, make_codec, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType


@dataclass(frozen=True, kw_only=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:  # pragma: no cover - not run via stub executor
        self.invocations.append(message.order_id)


class _StubExecutor(EndpointExecutor):
    def __init__(self, *, return_value: ExecutionOutcome) -> None:
        self.return_value = return_value
        self.calls: list[tuple[str, HandlerType]] = []

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.calls.append((envelope.message_type, handler_type))
        if on_result is not None:
            await on_result(self.return_value, None)
        return ExecutionResult(self.return_value)


class _Deps(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore, uow: IUnitOfWork) -> None:
        super().__init__()
        self._inbox = inbox
        self._uow = uow

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


_DESTINATION = handler_destination(_RecordingHandler)
_CODEC = make_codec()
_TYPE_REGISTRY = MessageTypeRegistry(identities={}, known_types=[_OrderPlaced])


def _abandoned_entry(
    inbox: FakeInboxStore,
    *,
    destination: str = _DESTINATION,
    source_uri: str = 'local://orders',
    attempts: int = 0,
) -> InboxEntry:
    envelope = make_envelope(_OrderPlaced(order_id='o-1'))
    entry = InboxEntry(
        id=envelope.message_id,
        payload=encode_payload(envelope, _CODEC),
        message_type=envelope.message_type,
        source_uri=EndpointUri(source_uri),
        destination=HandlerDestination(destination),
        owner_id=None,
        status=InboxStatus.INCOMING,
        attempts=attempts,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        metadata_=encode_metadata(envelope),
    )
    inbox.entries[entry.id, entry.destination] = entry
    return entry


class _DlqProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, dlq: IDeadLetterStore) -> None:
        super().__init__()
        self._dlq = dlq

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq


def _drainer(container: Any, executor: EndpointExecutor, *, max_attempts: int = 5) -> InboxDrainer:
    return InboxDrainer(
        container=container,
        codec=_CODEC,
        type_registry=_TYPE_REGISTRY,
        handler_by_fqn={_DESTINATION: _RecordingHandler},
        executor_factory=lambda _source_uri: executor,
        owner_id='node-a:1',
        keep_after_handled=timedelta(minutes=5),
        batch_size=100,
        max_attempts=max_attempts,
    )


class _CapturingExecutor(EndpointExecutor):
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    @override
    async def execute(
        self,
        envelope: Any,
        handler_type: HandlerType,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        self.envelopes.append(envelope)
        if on_result is not None:
            await on_result(ExecutionOutcome.SUCCESS, None)
        return ExecutionResult(ExecutionOutcome.SUCCESS)


async def test_drain_crash_recovery_rebuilds_envelope_from_decomposed_row() -> None:
    # Verifies that the drainer reconstructs the full envelope from the decomposed inbox row
    # (encoded payload + metadata_ + typed correlation_id/causation_id columns) without using
    # serializer.deserialize. A real PayloadCodec + MessageTypeRegistry are used — no mocks.
    inbox = FakeInboxStore()
    envelope = make_envelope(_OrderPlaced(order_id='o-99'))
    entry = InboxEntry(
        id=envelope.message_id,
        payload=encode_payload(envelope, _CODEC),
        message_type=envelope.message_type,
        source_uri=EndpointUri('local://orders'),
        destination=HandlerDestination(_DESTINATION),
        owner_id=None,
        status=InboxStatus.INCOMING,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        metadata_=encode_metadata(envelope),
    )
    inbox.entries[entry.id, entry.destination] = entry

    executor = _CapturingExecutor()
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor).drain_once()

    assert processed == 1
    assert len(executor.envelopes) == 1
    rebuilt = executor.envelopes[0]
    assert rebuilt.message_id == envelope.message_id
    assert rebuilt.correlation_id == envelope.correlation_id
    assert rebuilt.causation_id == envelope.causation_id
    assert rebuilt.message_type == envelope.message_type
    assert rebuilt.payload.order_id == 'o-99'


async def test_drain_executes_and_marks_handled_on_success() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor).drain_once()
    assert processed == 1
    assert executor.calls == [(entry.message_type, _RecordingHandler)]
    assert inbox.entries[entry.id, entry.destination].status is InboxStatus.HANDLED


async def test_drain_deletes_row_on_dead_letter() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    executor = _StubExecutor(return_value=ExecutionOutcome.DEAD_LETTERED)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        await _drainer(container, executor).drain_once()
    # delete must be the outcome of execution, not a bypass that skips the handler
    assert executor.calls == [(entry.message_type, _RecordingHandler)]
    assert (entry.id, entry.destination) not in inbox.entries


async def test_drain_poison_unknown_handler_under_cap_bumps_attempts_and_leaves_claimed() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox, destination='tests.GoneHandler')
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor, max_attempts=3).drain_once()
    assert processed == 0
    assert executor.calls == []
    stored = inbox.entries[entry.id, entry.destination]
    assert stored.status is InboxStatus.INCOMING
    assert stored.attempts == 1


async def test_drain_poison_unrebuildable_payload_bumps_attempts() -> None:
    # A row with metadata_=None causes wire_metadata_from_entry to return timestamp=None,
    # which makes rebuild_envelope raise ValueError — poison path, not deserialized.
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    inbox.entries[entry.id, entry.destination] = replace(entry, metadata_=None)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(
            container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS), max_attempts=3
        ).drain_once()
    assert processed == 0
    assert inbox.entries[entry.id, entry.destination].attempts == 1


async def test_drain_poison_at_cap_dead_letters_and_deletes() -> None:
    inbox = FakeInboxStore()
    dlq = RecordingDeadLetterStore()
    entry = _abandoned_entry(inbox, destination='tests.GoneHandler', attempts=2)
    async with make_async_container(_Deps(inbox, FakeUoW()), _DlqProvider(dlq)) as container:
        await _drainer(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS), max_attempts=3).drain_once()
    assert (entry.id, entry.destination) not in inbox.entries
    assert len(dlq.entries) == 1
    assert dlq.entries[0].message_type == entry.message_type


async def test_drain_poison_at_cap_without_dlq_store_deletes() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox, destination='tests.GoneHandler', attempts=2)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        await _drainer(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS), max_attempts=3).drain_once()
    assert (entry.id, entry.destination) not in inbox.entries


async def test_drain_isolates_poison_from_healthy_entries_in_a_batch() -> None:
    inbox = FakeInboxStore()
    good = _abandoned_entry(inbox)
    poison = _abandoned_entry(inbox, destination='tests.GoneHandler')
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor, max_attempts=3).drain_once()
    assert processed == 1
    assert inbox.entries[good.id, good.destination].status is InboxStatus.HANDLED
    assert inbox.entries[poison.id, poison.destination].attempts == 1


async def test_drain_skips_already_owned_incoming() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    # Already claimed by another node -> the drain's owner_id IS NULL filter must skip it (no re-claim,
    # no re-execution). Empty-inbox alone can't distinguish "skips owned" from "nothing to claim".
    inbox.entries[entry.id, entry.destination] = replace(entry, owner_id='other-node:1')
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor).drain_once()
    assert processed == 0
    assert executor.calls == []
    assert inbox.entries[entry.id, entry.destination].owner_id == 'other-node:1'


class _IncrementRaisesStore(FakeInboxStore):
    @override
    async def increment_attempts(self, entry_id: UUID, destination: str) -> None:
        msg = 'increment_attempts unavailable'
        raise ConnectionError(msg)


async def test_drain_logs_and_continues_when_an_entry_raises() -> None:
    inbox = _IncrementRaisesStore()
    good = _abandoned_entry(inbox)
    poison = _abandoned_entry(inbox, destination='tests.GoneHandler')
    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor, max_attempts=3).drain_once()
    assert processed == 1
    assert inbox.entries[good.id, good.destination].status is InboxStatus.HANDLED
    assert inbox.entries[poison.id, poison.destination].status is InboxStatus.INCOMING


async def test_drain_deferred_terminal_under_cap_bumps_attempts_and_leaves_claimed() -> None:
    # A REQUEUE/PAUSE outcome can't be enacted on the recovery path (no live listener), so it is bounded
    # like poison: under the cap the row is left INCOMING with attempts bumped — never an endless oscillation.
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED)
    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        processed = await _drainer(container, executor, max_attempts=3).drain_once()
    assert processed == 0
    assert executor.calls == [(entry.message_type, _RecordingHandler)]  # the handler DID run
    stored = inbox.entries[entry.id, entry.destination]
    assert stored.status is InboxStatus.INCOMING
    assert stored.attempts == 1


async def test_drain_deferred_terminal_at_cap_dead_letters() -> None:
    inbox = FakeInboxStore()
    dlq = RecordingDeadLetterStore()
    entry = _abandoned_entry(inbox, attempts=2)
    executor = _StubExecutor(return_value=ExecutionOutcome.PAUSED)
    async with make_async_container(_Deps(inbox, FakeUoW()), _DlqProvider(dlq)) as container:
        await _drainer(container, executor, max_attempts=3).drain_once()
    assert (entry.id, entry.destination) not in inbox.entries  # bounded -> dead-lettered + deleted at the cap
    assert len(dlq.entries) == 1


async def test_drain_poison_at_cap_reads_correlation_from_typed_columns_not_payload() -> None:
    # NON-VACUOUS poison test: the payload is a real encoded dict (which never contains
    # correlation_id / causation_id keys). Proves _poison_dead_letter reads the typed
    # columns, not the payload blob — if it still read entry.payload.get('correlation_id')
    # it would fall back to entry.id, not the real UUIDs.
    inbox = FakeInboxStore()
    dlq = RecordingDeadLetterStore()
    expected_correlation = uuid4()
    expected_causation = uuid4()
    entry = _abandoned_entry(inbox, destination='tests.GoneHandler', attempts=2)
    # Real encoded payload — contains only the message's own fields, NOT correlation/causation.
    real_payload = encode_payload(make_envelope(_OrderPlaced(order_id='o-1')), _CODEC)
    assert 'correlation_id' not in real_payload
    assert 'causation_id' not in real_payload
    inbox.entries[entry.id, entry.destination] = replace(
        entry,
        payload=real_payload,
        correlation_id=expected_correlation,
        causation_id=expected_causation,
    )
    async with make_async_container(_Deps(inbox, FakeUoW()), _DlqProvider(dlq)) as container:
        await _drainer(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS), max_attempts=3).drain_once()
    assert len(dlq.entries) == 1
    assert dlq.entries[0].correlation_id == expected_correlation
    assert dlq.entries[0].causation_id == expected_causation


async def test_drain_crash_recovery_keyed_on_scheme_qualified_source_uri() -> None:
    # An inbox row persisted by the inbound listener carries a real scheme'd source_uri
    # (e.g. 'rabbitmq://orders').  The drainer must pass that exact value to executor_factory
    # so the crash-recovery executor is correctly keyed; the handler must still be resolved
    # from destination (FQN), not from the URI.
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox, source_uri='rabbitmq://orders')

    executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
    factory_calls: list[str] = []

    def capturing_factory(source_uri: str) -> EndpointExecutor:
        factory_calls.append(source_uri)
        return executor

    async with make_async_container(_Deps(inbox, FakeUoW())) as container:
        drainer = InboxDrainer(
            container=container,
            codec=_CODEC,
            type_registry=_TYPE_REGISTRY,
            handler_by_fqn={_DESTINATION: _RecordingHandler},
            executor_factory=capturing_factory,
            owner_id='node-a:1',
            keep_after_handled=timedelta(minutes=5),
            batch_size=100,
            max_attempts=5,
        )
        processed = await drainer.drain_once()

    assert processed == 1
    assert factory_calls == ['rabbitmq://orders']
    assert executor.calls == [(entry.message_type, _RecordingHandler)]
    assert inbox.entries[entry.id, entry.destination].status is InboxStatus.HANDLED
