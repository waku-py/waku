from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox._destination import handler_destination  # noqa: PLC2701
from waku.messaging.inbox.drainer import InboxDrainer
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingDeadLetterStore, make_envelope, make_serializer
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
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
    async def execute(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> ExecutionOutcome:
        self.calls.append((envelope.message_type, handler_type))
        return self.return_value


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


def _abandoned_entry(
    inbox: FakeInboxStore,
    *,
    destination: str = _DESTINATION,
    source_uri: str = 'local://orders',
    attempts: int = 0,
) -> InboxEntry:
    serializer = make_serializer(_OrderPlaced)
    envelope = make_envelope(_OrderPlaced(order_id='o-1'))
    entry = InboxEntry(
        id=envelope.message_id,
        payload=serializer.serialize(envelope),
        message_type=envelope.message_type,
        source_uri=source_uri,
        destination=destination,
        owner_id=None,
        status=InboxStatus.INCOMING,
        attempts=attempts,
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
        serializer=make_serializer(_OrderPlaced),
        handler_by_fqn={_DESTINATION: _RecordingHandler},
        executor_factory=lambda _source_uri: executor,
        owner_id='node-a:1',
        keep_after_handled=timedelta(minutes=5),
        batch_size=100,
        max_attempts=max_attempts,
    )


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


async def test_drain_poison_undeserializable_payload_bumps_attempts() -> None:
    inbox = FakeInboxStore()
    entry = _abandoned_entry(inbox)
    inbox.entries[entry.id, entry.destination] = replace(entry, payload={'message_type': 'tests.Unknown'})
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


async def test_drain_poison_at_cap_falls_back_to_message_id_for_bad_or_absent_ids() -> None:
    # correlation_id present but not a UUID (-> ValueError branch); causation_id absent (-> non-str branch).
    inbox = FakeInboxStore()
    dlq = RecordingDeadLetterStore()
    entry = _abandoned_entry(inbox, destination='tests.GoneHandler', attempts=2)
    inbox.entries[entry.id, entry.destination] = replace(
        entry,
        payload={'message_type': 'tests.Unknown', 'correlation_id': 'not-a-uuid'},
    )
    async with make_async_container(_Deps(inbox, FakeUoW()), _DlqProvider(dlq)) as container:
        await _drainer(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS), max_attempts=3).drain_once()
    assert len(dlq.entries) == 1
    assert dlq.entries[0].correlation_id == entry.id
    assert dlq.entries[0].causation_id == entry.id
