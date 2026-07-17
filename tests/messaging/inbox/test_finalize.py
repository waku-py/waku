from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from dishka import Provider, Scope, make_async_container, provide

from waku._internal.transaction import RollbackFailedError, TransactionExecutionError
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.execution import TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.inbox import EndpointUri, HandlerDestination
from waku.messaging.inbox._internal.finalize import apply_inbox_outcome
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.uow import IUnitOfWork

from tests.messaging.helpers import RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore


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


def _seed(inbox: FakeInboxStore) -> tuple[UUID, str]:
    entry_id = uuid4()
    destination = 'tests.Handler'
    inbox.entries[entry_id, destination] = InboxEntry(
        id=entry_id,
        payload={},
        message_type='tests.Event',
        source_uri=EndpointUri('local://q'),
        destination=HandlerDestination(destination),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        owner_id='node-a:1',
    )
    return entry_id, destination


def _intent(outcome: ExecutionOutcome) -> TerminalIntent:
    kinds = {
        ExecutionOutcome.SUCCESS: TerminalIntentKind.SUCCESS,
        ExecutionOutcome.DISCARDED: TerminalIntentKind.DISCARD,
        ExecutionOutcome.FAILED_NO_POLICY: TerminalIntentKind.FAILED_NO_POLICY,
        ExecutionOutcome.REQUEUED: TerminalIntentKind.REQUEUE,
        ExecutionOutcome.PAUSED: TerminalIntentKind.PAUSE,
    }
    return TerminalIntent(kinds[outcome])


@pytest.mark.parametrize(
    'outcome',
    [ExecutionOutcome.DISCARDED, ExecutionOutcome.FAILED_NO_POLICY],
)
async def test_non_success_deletes(outcome: ExecutionOutcome) -> None:
    inbox = FakeInboxStore()
    uow = RecordingUoW()
    entry_id, destination = _seed(inbox)
    async with make_async_container(_Deps(inbox, uow)) as container:
        await apply_inbox_outcome(
            container,
            entry_id=entry_id,
            destination=destination,
            intent=_intent(outcome),
            keep_after_handled=timedelta(minutes=5),
        )
    assert (entry_id, destination) not in inbox.entries
    assert uow.commit_count == 1


async def test_deferred_terminal_outcome_rolls_back() -> None:
    inbox = FakeInboxStore()
    uow = RecordingUoW()
    entry_id, destination = _seed(inbox)
    async with make_async_container(_Deps(inbox, uow)) as container:
        with pytest.raises(RuntimeError, match='must be intercepted'):
            await apply_inbox_outcome(
                container,
                entry_id=entry_id,
                destination=destination,
                intent=_intent(ExecutionOutcome.REQUEUED),
                keep_after_handled=timedelta(minutes=5),
            )
    assert uow.rollback_count == 1
    assert uow.commit_count == 0


async def test_terminal_transition_failed_rollback_is_fatal() -> None:
    # The row transition commits, but commit fails and its rollback also fails: uniformly fatal. The
    # failure surfaces as a RollbackFailedError fatal with the primary preserved, never a
    # silently-logged cleanup that lets a broken transaction pass as a normal terminal.
    inbox = FakeInboxStore()
    commit_error = RuntimeError('commit failed')
    rollback_error = RuntimeError('rollback failed')
    uow = RecordingUoW(commit_error=commit_error, rollback_error=rollback_error)
    entry_id, destination = _seed(inbox)
    async with make_async_container(_Deps(inbox, uow)) as container:
        with pytest.raises(TransactionExecutionError) as raised:
            await apply_inbox_outcome(
                container,
                entry_id=entry_id,
                destination=destination,
                intent=_intent(ExecutionOutcome.SUCCESS),
                keep_after_handled=timedelta(minutes=5),
            )

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is rollback_error
    assert raised.value.primary_error is commit_error


async def test_success_marks_handled_and_commits_without_rollback() -> None:
    inbox = FakeInboxStore()
    uow = RecordingUoW()
    entry_id, destination = _seed(inbox)
    async with make_async_container(_Deps(inbox, uow)) as container:
        await apply_inbox_outcome(
            container,
            entry_id=entry_id,
            destination=destination,
            intent=_intent(ExecutionOutcome.SUCCESS),
            keep_after_handled=timedelta(minutes=5),
        )
    assert inbox.entries[entry_id, destination].status is InboxStatus.HANDLED
    assert uow.commit_count == 1
    assert uow.rollback_count == 0
