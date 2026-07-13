from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messages import IEvent
from waku.messaging._internal.pauser import TimedPauser
from waku.messaging.endpoints._internal.redelivery import RedeliveryCoordinator, RedeliveryHooks
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
from waku.messaging.endpoints.executor import ExecutionResult
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.handler import EventHandler

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType


@dataclass
class _Event(IEvent):
    kind: str


class _Handler(EventHandler[_Event]):
    @override
    async def handle(self, message: _Event, /) -> None: ...


class _FakeWorker(MemoryStreamWorker[Any]):
    """Coordinator only reads ``is_running`` and calls ``try_send`` — bypass the real stream lifecycle."""

    def __init__(self, *, running: bool = True, accept: bool = True) -> None:
        self.running = running
        self.accept = accept
        self.sent: list[Any] = []

    @property
    @override
    def is_running(self) -> bool:
        return self.running

    @override
    def try_send(self, item: Any) -> bool:
        if not self.accept:
            return False
        self.sent.append(item)
        return True


class _RecordingPauser(TimedPauser):
    def __init__(self) -> None:
        self.pauses: list[timedelta] = []

    @override
    async def pause(self, duration: timedelta) -> None:
        self.pauses.append(duration)


class _RecordingHooks:
    def __init__(self) -> None:
        self.dead_lettered: list[int] = []
        self.stopped: list[int] = []
        self.attempts: list[HandlerType] = []
        self.finalized: list[ExecutionOutcome] = []

    async def dead_letter(self, _envelope: MessageEnvelope[Any], _handler_type: HandlerType, attempts: int) -> None:
        self.dead_lettered.append(attempts)

    async def on_stopped(self, _envelope: MessageEnvelope[Any], _handler_type: HandlerType, attempts: int) -> None:
        self.stopped.append(attempts)

    async def record_attempt(self, _envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        self.attempts.append(handler_type)

    async def finalize(
        self,
        _envelope: MessageEnvelope[Any],
        _handler_type: HandlerType,
        outcome: ExecutionOutcome,
    ) -> None:
        self.finalized.append(outcome)


def _hooks(rec: _RecordingHooks) -> RedeliveryHooks:
    return RedeliveryHooks(
        dead_letter=rec.dead_letter,
        on_stopped=rec.on_stopped,
        record_attempt=rec.record_attempt,
        finalize=rec.finalize,
    )


def _coordinator(
    worker: MemoryStreamWorker[Any],
    pauser: TimedPauser,
    rec: _RecordingHooks,
    *,
    max_requeue_attempts: int = 5,
) -> RedeliveryCoordinator:
    return RedeliveryCoordinator(
        worker=worker,
        timed_pauser=pauser,
        max_requeue_attempts=max_requeue_attempts,
        hooks=_hooks(rec),
    )


class TestRedeliveryCoordinator:
    @staticmethod
    async def test_requeue_under_budget_re_enqueues_the_failing_handler_and_records_the_attempt() -> None:
        worker, pauser, rec = _FakeWorker(), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec)
        envelope = make_envelope(_Event(kind='order'))

        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.REQUEUED))

        assert worker.sent == [(envelope, frozenset({_Handler}))]
        assert rec.attempts == [_Handler]
        assert rec.dead_lettered == []
        assert rec.stopped == []
        assert pauser.pauses == []

    @staticmethod
    async def test_budget_exhaustion_dead_letters_at_the_bound_and_never_re_pauses() -> None:
        worker, pauser, rec = _FakeWorker(), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec, max_requeue_attempts=2)
        envelope = make_envelope(_Event(kind='order'))
        paused = ExecutionResult(ExecutionOutcome.PAUSED, pause_duration=timedelta(seconds=1))

        await coordinator.handle_result(envelope, _Handler, paused)  # count 1 < 2 -> re-enqueue + pause
        await coordinator.handle_result(envelope, _Handler, paused)  # count 2 >= 2 -> dead-letter, no re-pause

        assert rec.dead_lettered == [2]
        assert worker.sent == [(envelope, frozenset({_Handler}))]  # only the first call re-enqueued
        assert pauser.pauses == [timedelta(seconds=1)]  # no second pause -> no livelock
        assert rec.attempts == [_Handler, _Handler]  # record_attempt fires before the budget check both times

    @staticmethod
    async def test_full_buffer_while_running_dead_letters_without_blocking() -> None:
        worker, pauser, rec = _FakeWorker(accept=False), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec)
        envelope = make_envelope(_Event(kind='order'))

        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.REQUEUED))

        assert rec.dead_lettered == [1]
        assert rec.stopped == []
        assert worker.sent == []

    @staticmethod
    async def test_stopped_worker_defers_to_on_stopped_without_dead_lettering() -> None:
        worker, pauser, rec = _FakeWorker(running=False), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec)
        envelope = make_envelope(_Event(kind='order'))

        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.REQUEUED))

        assert rec.stopped == [1]
        assert rec.dead_lettered == []
        assert worker.sent == []

    @staticmethod
    async def test_non_deferred_outcome_finalizes() -> None:
        worker, pauser, rec = _FakeWorker(), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec)
        envelope = make_envelope(_Event(kind='order'))

        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.SUCCESS))

        assert rec.finalized == [ExecutionOutcome.SUCCESS]
        assert rec.dead_lettered == []
        assert worker.sent == []

    @staticmethod
    async def test_finalize_clears_the_budget_so_a_later_requeue_starts_fresh() -> None:
        worker, pauser, rec = _FakeWorker(), _RecordingPauser(), _RecordingHooks()
        coordinator = _coordinator(worker, pauser, rec, max_requeue_attempts=2)
        envelope = make_envelope(_Event(kind='order'))

        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.REQUEUED))  # count 1
        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.SUCCESS))  # pop the count
        await coordinator.handle_result(envelope, _Handler, ExecutionResult(ExecutionOutcome.REQUEUED))  # count 1 again

        assert rec.finalized == [ExecutionOutcome.SUCCESS]
        assert rec.dead_lettered == []  # the post-success requeue was NOT treated as count 2
        assert worker.sent == [(envelope, frozenset({_Handler})), (envelope, frozenset({_Handler}))]
