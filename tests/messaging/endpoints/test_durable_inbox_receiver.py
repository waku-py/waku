from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

import anyio
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from waku.messaging.endpoints.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionResult
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.pauser import PauseToken
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    make_codec,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pytest_mock import MockerFixture


@dataclass
class _Event(IEvent):
    kind: str


class _Handler(EventHandler[_Event]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _Event, /) -> None:
        self.invocations.append(message.kind)


class _DepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore, dlq: IDeadLetterStore) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._codec = make_codec()
        self._uow: IUnitOfWork = FakeUoW()
        self._allocator: ISequenceAllocator = RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def allocator(self) -> ISequenceAllocator:
        return self._allocator


class _StubExecutor(EndpointExecutor):
    def __init__(self, *, return_value: ExecutionOutcome, requeue_limit: int | None = None) -> None:
        # Bypass parent __init__: tests don't exercise real dispatch.
        self.return_value = return_value
        self.requeue_limit = requeue_limit
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: object,
        handler_type: object,
        *,
        on_result: object = None,
    ) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(outcome=self.return_value, pause_duration=None, requeue_limit=self.requeue_limit)


def _receiver(
    container: AsyncContainer,
    executor: EndpointExecutor,
    *,
    max_requeue_attempts: int = 5,
    max_buffer_size: float = 100,
    stop_timeout: float = 1.0,
) -> DurableInboxReceiver:
    return DurableInboxReceiver(
        uri='local://test',
        container=container,
        executor=executor,
        inbox_owner_id='node-a:1',
        keep_after_handled=timedelta(seconds=300),
        max_requeue_attempts=max_requeue_attempts,
        max_buffer_size=max_buffer_size,
        stop_timeout=stop_timeout,
    )


class TestDurableInboxReceiverPersist:
    @staticmethod
    async def test_persist_returns_only_fresh_handlers() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == handler_types

    @staticmethod
    async def test_persist_re_persisting_same_id_and_handler_returns_empty() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            await receiver.persist(envelope, handler_types)
            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == frozenset()


class TestDurableInboxReceiverProcess:
    @staticmethod
    async def test_enqueue_and_success_marks_inbox_row_handled() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Shipped'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await receiver.stop()

        assert executor.calls == 1
        rows = list(inbox.entries.values())
        assert len(rows) == 1
        assert rows[0].status is InboxStatus.HANDLED

    @staticmethod
    async def test_requeue_increments_attempts_and_reprocesses() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            call_count = 0

            class _RequeueOnceThenSucceed(_StubExecutor):
                @override
                async def execute(
                    self,
                    envelope: object,
                    handler_type: object,
                    *,
                    on_result: object = None,
                ) -> ExecutionResult:
                    nonlocal call_count
                    call_count += 1
                    outcome = ExecutionOutcome.REQUEUED if call_count == 1 else ExecutionOutcome.SUCCESS
                    return ExecutionResult(outcome=outcome, pause_duration=None)

            executor = _RequeueOnceThenSucceed(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Billed'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: call_count >= 2)
            await receiver.stop()

        assert call_count == 2  # requeued once then succeeded
        row = next(iter(inbox.entries.values()))
        assert row.status is InboxStatus.HANDLED

    @staticmethod
    async def test_exceeding_max_requeue_attempts_moves_to_dead_letter() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED)
            receiver = _receiver(container, executor, max_requeue_attempts=3, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='Poison'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_lettered) == 1)
            await receiver.stop()

        assert len(inbox.dead_lettered) == 1

    @staticmethod
    async def test_per_rule_budget_dead_letters_below_endpoint_bound() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED, requeue_limit=2)
            receiver = _receiver(container, executor, max_requeue_attempts=5, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='BudgetTwo'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_lettered) == 1)
            await receiver.stop()

        assert executor.calls == 2  # per-rule budget dead-letters below the endpoint's 5

    @staticmethod
    async def test_distinct_per_rule_budget_honored_independently() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED, requeue_limit=4)
            receiver = _receiver(container, executor, max_requeue_attempts=5, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='BudgetFour'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_lettered) == 1)
            await receiver.stop()

        assert executor.calls == 4  # a different per-rule budget honored independently of the endpoint's 5


class _ObservingExecutor(EndpointExecutor):
    """Feeds the supplied outcome to ``on_result`` (like the real executor) so an attached breaker records it."""

    def __init__(self, *, outcome: ExecutionOutcome, exc: Exception | None) -> None:
        self._outcome = outcome
        self._exc = exc

    @override
    async def execute(
        self,
        envelope: object,
        handler_type: object,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        if on_result is not None:
            await on_result(self._outcome, self._exc)
        return ExecutionResult(outcome=self._outcome, pause_duration=None)


class _BlockingExecutor(EndpointExecutor):
    """Parks in the handler until released, so buffered items stay queued and queue_depth is observable."""

    def __init__(self, *, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release

    @override
    async def execute(
        self,
        envelope: object,
        handler_type: object,
        *,
        on_result: object = None,
    ) -> ExecutionResult:
        self._started.set()
        await self._release.wait()
        return ExecutionResult(outcome=ExecutionOutcome.SUCCESS, pause_duration=None)


class TestDurableInboxReceiverBackpressureSeams:
    @staticmethod
    async def test_attach_circuit_breaker_feeds_execution_outcomes() -> None:
        inbox = FakeInboxStore()
        pauses: list[str] = []

        async def pause() -> PauseToken:  # noqa: RUF029
            pauses.append('pause')
            return PauseToken()

        async def resume(token: PauseToken) -> None:  # noqa: ARG001, RUF029
            pauses.append('resume')

        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(
                container,
                _ObservingExecutor(outcome=ExecutionOutcome.FAILED_NO_POLICY, exc=RuntimeError()),
            )
            breaker = CircuitBreaker(
                config=CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=1),
                pause=pause,
                resume=resume,
            )
            receiver.attach_circuit_breaker(breaker)
            envelope = make_envelope(_Event(kind='Boom'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: breaker.state is CircuitState.OPEN)
            await receiver.stop()  # aclose()s the attached breaker exactly once, cancelling its parked resume

        assert pauses == ['pause']  # the attached breaker tripped → it drove its pause callback

    @staticmethod
    async def test_no_circuit_breaker_never_pauses(mocker: MockerFixture) -> None:
        # No breaker attached => the PassthroughCircuitBreaker default: failing outcomes are recorded to
        # nothing and the receiver never pauses.
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(
                container,
                _ObservingExecutor(outcome=ExecutionOutcome.FAILED_NO_POLICY, exc=RuntimeError()),
            )
            pause_spy = mocker.spy(DurableInboxReceiver, 'pause')
            envelope = make_envelope(_Event(kind='Boom'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: not inbox.entries)  # FAILED_NO_POLICY finalizes (deletes) the row
            await receiver.stop()

        pause_spy.assert_not_called()

    @staticmethod
    async def test_queue_depth_reflects_buffered_items() -> None:
        inbox = FakeInboxStore()
        started, release = asyncio.Event(), asyncio.Event()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(container, _BlockingExecutor(started=started, release=release), max_buffer_size=10)

            await receiver.start()
            for kind in ('a', 'b'):
                env = make_envelope(_Event(kind=kind))
                fresh = await receiver.persist(env, frozenset([_Handler]))
                await receiver.enqueue(env, fresh)
            with anyio.fail_after(5):
                await started.wait()  # first item pulled into the parked handler; the second stays buffered

            assert receiver.queue_depth == 1

            release.set()
            await receiver.stop()
