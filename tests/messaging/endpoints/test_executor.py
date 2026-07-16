from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator  # noqa: TC003 -- Dishka resolves provider annotations at runtime
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import anyio
import anyio.lowlevel
import pytest
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import TransactionExecutionError, TransactionFailureKind
from waku.backends.memory import MemoryBackend
from waku.di import object_, provider
from waku.messages import IEvent
from waku.messaging import (
    EndpointDefaults,
    EndpointMode,
    EventHandler,
    IMessageBus,
    IOutgoingMessages,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    local_queue,
    route,
)
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore
from waku.messaging.endpoints._internal.execution import TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints.executor import (
    EndpointExecutor,
    EndpointExecutorFactory,
    materialize_standalone_dead_letter,
)
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.exceptions import HandlerTimeoutError
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    NOOP_EVALUATOR,
    FailingDeadLetterStore,
    RecordingDeadLetterStore,
    RecordingDurabilityStore,
    RecordingUoW,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from typing import Any

    from waku.application import WakuApplication
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.dead_letter import DeadLetterEntry


@dataclass(frozen=True, slots=True)
class _FailingCommand(IRequest[None]):
    value: str


@dataclass(frozen=True, slots=True)
class _CascadeEvent(IEvent):
    value: str


@dataclass(frozen=True, slots=True)
class _PolicyDeadLetterEvent(IEvent):
    value: str


class _PolicyDeadLetterHandler(EventHandler[_PolicyDeadLetterEvent]):
    calls: ClassVar[int] = 0

    @override
    async def handle(self, event: _PolicyDeadLetterEvent, /) -> None:
        type(self).calls += 1
        msg = f'policy dead letter: {event.value}'
        raise RuntimeError(msg)


class _ScopeProbe: ...


class _TracingUoW(RecordingUoW):
    def __init__(self, trace: list[str], identity: int) -> None:
        super().__init__()
        self._trace = trace
        self.identity = identity

    @override
    async def commit(self) -> None:
        self._trace.append(f'commit:{self.identity}')
        await super().commit()

    @override
    async def rollback(self) -> None:
        self._trace.append(f'rollback:{self.identity}')
        await super().rollback()


def _make_fail_n_times_handler(
    fail_count: int = 1,
) -> tuple[type[RequestHandler[_FailingCommand, None]], list[int]]:
    calls: list[int] = []

    class Handler(RequestHandler[_FailingCommand, None]):
        @override
        async def handle(self, request: _FailingCommand, /) -> None:
            calls.append(1)
            if len(calls) <= fail_count:
                msg = 'transient failure'
                raise RuntimeError(msg)

    return Handler, calls


def _make_always_fail_handler() -> tuple[type[RequestHandler[_FailingCommand, None]], list[int]]:
    calls: list[int] = []

    class Handler(RequestHandler[_FailingCommand, None]):
        @override
        async def handle(self, request: _FailingCommand, /) -> None:
            calls.append(1)
            msg = 'permanent failure'
            raise ValueError(msg)

    return Handler, calls


def _evaluator_for(policy: ErrorPolicy) -> ErrorPolicyEvaluator:
    return ErrorPolicyEvaluator(ErrorPolicyRegistry(handler_policies={}, default_policies=(policy,)))


def _dead_letter_intent() -> TerminalIntent:
    return TerminalIntent(TerminalIntentKind.DEAD_LETTER, error=ValueError('permanent failure'), attempt=1)


def _make_observer() -> tuple[
    Callable[[ExecutionOutcome, Exception | None], Awaitable[None]],
    list[tuple[ExecutionOutcome, Exception | None]],
]:
    recorded: list[tuple[ExecutionOutcome, Exception | None]] = []

    async def observer(outcome: ExecutionOutcome, exc: Exception | None) -> None:  # noqa: RUF029
        recorded.append((outcome, exc))

    return observer, recorded


class _ActionRecordingDeadLetterStore(RecordingDeadLetterStore):
    def __init__(self, actions: list[str]) -> None:
        super().__init__()
        self._actions = actions

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self._actions.append('save')
        await super().save(entry)


class _ActionRecordingUoW(IUnitOfWork):
    def __init__(
        self,
        actions: list[str],
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        cancel_scope: anyio.CancelScope | None = None,
    ) -> None:
        self._actions = actions
        self._commit_error = commit_error
        self._rollback_error = rollback_error
        self._cancel_scope = cancel_scope

    @override
    async def commit(self) -> None:
        self._actions.append('commit')
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()
            await anyio.lowlevel.checkpoint()
        if self._commit_error is not None:
            raise self._commit_error

    @override
    async def rollback(self) -> None:
        await anyio.lowlevel.checkpoint()
        self._actions.append('rollback')
        if self._rollback_error is not None:
            raise self._rollback_error


def _durability(dead_letters: IDeadLetterStore, unit_of_work: IUnitOfWork) -> RecordingDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=FakeInboxStore(),
        dead_letters=dead_letters,
    )


async def _make_executor(
    app: WakuApplication,
    evaluator: ErrorPolicyEvaluator,
    *,
    uri: str = 'test://q',
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    now: Callable[[], datetime] = utc_now,
    dead_letter_capable: bool = True,
) -> EndpointExecutor:
    invoker = await app.container.get(HandlerPipelineInvoker)
    return EndpointExecutor(
        container=app.container,
        evaluator=evaluator,
        endpoint_uri=uri,
        invoker=invoker,
        observers=MessageObservers([]),
        sleep=sleep,
        now=now,
        dead_letter_capable=dead_letter_capable,
    )


async def _run_executor(
    handler: type[RequestHandler[_FailingCommand, None]],
    evaluator: ErrorPolicyEvaluator,
) -> ExecutionOutcome:
    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='test'))
        outcome = (await executor.execute(envelope, handler)).outcome
        return outcome


async def test_executor_transient_retried() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=3))
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.SUCCESS


async def test_executor_exhausted_retries() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=2))
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.DISCARDED  # exhausted retries + no fallback → evaluator terminal = DISCARD


async def test_executor_transient_backoff() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(
        ErrorPolicy.on_any_exception().retry_with_backoff(
            max_attempts=3,
            base_delay=timedelta(milliseconds=1),
            max_delay=timedelta(milliseconds=10),
        ),
    )
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.SUCCESS


async def test_retry_with_backoff_sleeps_for_the_policy_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, _ = _make_fail_n_times_handler(fail_count=1)
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:  # noqa: RUF029
        recorded.append(delay)

    # Pin jitter to a fixed value so the recorded delay is exact, not merely in-range.
    # Plain RETRY carries retry_delay=None (no sleep), keeping this test mutation-distinct.
    monkeypatch.setattr('waku.messaging._internal.escalation.calculate_backoff_with_jitter', lambda *_a, **_kw: 5.0)
    evaluator = _evaluator_for(
        ErrorPolicy
        .on_any_exception()
        .retry_with_backoff(max_attempts=2, base_delay=timedelta(seconds=5), max_delay=timedelta(seconds=5))
        .then_move_to_dead_letter(),
    )

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator, sleep=fake_sleep)
        envelope = make_envelope(_FailingCommand(value='retry-backoff'))
        outcome = (await executor.execute(envelope, handler)).outcome

    assert outcome is ExecutionOutcome.SUCCESS
    assert recorded == [5.0]


async def test_executor_discard() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().discard())
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 1
    assert outcome is ExecutionOutcome.DISCARDED


async def test_executor_no_policy() -> None:
    handler, calls = _make_always_fail_handler()
    outcome = await _run_executor(handler, NOOP_EVALUATOR)
    assert len(calls) == 1
    assert outcome is ExecutionOutcome.FAILED_NO_POLICY


async def test_executor_requeue_policy_surfaces_requeued_outcome() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().requeue())

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='requeue'))
        result = await executor.execute(envelope, handler)

    assert len(calls) == 1  # REQUEUE fires once, no inline retry
    assert result.outcome is ExecutionOutcome.REQUEUED
    assert result.pause_duration is None
    assert result.requeue_limit is None  # budget-less → endpoint fallback


async def test_executor_budgeted_requeue_surfaces_limit() -> None:
    handler, _ = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().requeue(max_attempts=7))

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='budgeted-requeue'))
        result = await executor.execute(envelope, handler)

    assert result.outcome is ExecutionOutcome.REQUEUED
    assert result.requeue_limit == 7


async def test_executor_budgeted_pause_surfaces_limit_and_duration() -> None:
    handler, _ = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().pause_processing(timedelta(seconds=1), max_attempts=2))

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='budgeted-pause'))
        result = await executor.execute(envelope, handler)

    assert result.outcome is ExecutionOutcome.PAUSED
    assert result.pause_duration == timedelta(seconds=1)
    assert result.requeue_limit == 2


class TestEndpointExecutorExpiry:
    _NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)

    @staticmethod
    async def test_execute_discards_expired_envelope_without_running_handler() -> None:
        handler, calls = _make_always_fail_handler()
        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(handler)],
        ) as app:
            executor = await _make_executor(app, NOOP_EVALUATOR, now=lambda: TestEndpointExecutorExpiry._NOW)
            expired = make_envelope(
                _FailingCommand(value='expired'),
                expires_at=TestEndpointExecutorExpiry._NOW - timedelta(seconds=1),
            )
            result = await executor.execute(expired, handler)

        assert result.outcome is ExecutionOutcome.DISCARDED  # not FAILED_NO_POLICY: handler never ran
        assert calls == []

    @staticmethod
    async def test_execute_runs_handler_when_not_expired() -> None:
        handler, calls = _make_fail_n_times_handler(fail_count=0)
        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(handler)],
        ) as app:
            executor = await _make_executor(app, NOOP_EVALUATOR, now=lambda: TestEndpointExecutorExpiry._NOW)
            live = make_envelope(
                _FailingCommand(value='live'),
                expires_at=TestEndpointExecutorExpiry._NOW + timedelta(seconds=60),
            )
            result = await executor.execute(live, handler)

        assert result.outcome is ExecutionOutcome.SUCCESS
        assert calls == [1]


class TestEndpointExecutorTransactionCleanup:
    @staticmethod
    async def test_rollback_failure_escapes_without_retry_or_terminal_outcome() -> None:
        handler, calls = _make_always_fail_handler()
        rollback_error = RuntimeError('transaction rollback failed')
        uow = RecordingUoW(rollback_error=rollback_error)
        config = MessagingConfig(
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=2).then_discard(),),
            ),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='rollback-fail'))
            with pytest.raises(TransactionExecutionError) as raised:
                await executor.execute(envelope, handler)

        assert raised.value.kind is TransactionFailureKind.ROLLBACK_FAILED
        assert raised.value.error is rollback_error
        assert len(calls) == 1

    @staticmethod
    async def test_timeout_rollback_failure_escapes_without_retry_or_terminal_outcome() -> None:
        calls: list[int] = []
        rollback_error = RuntimeError('transaction rollback failed')
        actions: list[str] = []
        uow = _ActionRecordingUoW(actions, rollback_error=rollback_error)
        observer, recorded = _make_observer()

        class _BlockingHandler(RequestHandler[_FailingCommand, None]):
            execution_timeout = timedelta(seconds=0.01)

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                calls.append(1)
                await anyio.Event().wait()

        config = MessagingConfig(
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=2).then_discard(),),
            ),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            executor = await _make_executor(app, await app.container.get(ErrorPolicyEvaluator))
            envelope = make_envelope(_FailingCommand(value='timeout-rollback-fail'))
            with pytest.raises(TransactionExecutionError) as raised:
                await executor.execute(envelope, _BlockingHandler, on_result=observer)

        assert raised.value.kind is TransactionFailureKind.ROLLBACK_FAILED
        assert raised.value.error is rollback_error
        assert calls == [1]
        assert actions == ['rollback']
        assert recorded == []

    @staticmethod
    async def test_post_commit_scope_teardown_failure_does_not_retry_committed_handler() -> None:
        calls: list[int] = []
        teardown_error = RuntimeError('request scope teardown failed')
        uow = RecordingUoW()
        observer, recorded = _make_observer()

        class _SuccessfulHandler(RequestHandler[_FailingCommand, None]):
            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                calls.append(1)

        async def provide_uow() -> AsyncIterator[IUnitOfWork]:  # noqa: RUF029 -- Dishka async-generator provider
            yield uow
            raise teardown_error

        config = MessagingConfig(
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=2).then_discard(),),
            ),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_SuccessfulHandler)],
            providers=[provider(provide_uow, provided_type=IUnitOfWork)],
        ) as app:
            executor = await _make_executor(app, await app.container.get(ErrorPolicyEvaluator))
            envelope = make_envelope(_FailingCommand(value='post-commit-teardown'))
            with pytest.raises(TransactionExecutionError) as raised:
                await executor.execute(envelope, _SuccessfulHandler, on_result=observer)

        assert raised.value.kind is TransactionFailureKind.AFTER_COMMIT
        assert isinstance(raised.value.error, ExceptionGroup)
        assert raised.value.error.exceptions == (teardown_error,)
        assert calls == [1]
        assert uow.commit_count == 1
        assert uow.rollback_count == 0
        assert recorded == []

    @staticmethod
    async def test_transactional_cascade_flush_uses_fresh_scope_after_origin_exit() -> None:
        trace: list[str] = []
        uows: list[IUnitOfWork] = []

        def provide_uow() -> IUnitOfWork:
            uow = _TracingUoW(trace, len(uows) + 1)
            uows.append(uow)
            return uow

        async def provide_probe() -> AsyncIterator[_ScopeProbe]:
            await anyio.lowlevel.checkpoint()
            yield _ScopeProbe()
            trace.append('origin-exit')

        class _OuterHandler(RequestHandler[_FailingCommand, None]):
            def __init__(self, outgoing: IOutgoingMessages, uow: IUnitOfWork, _probe: _ScopeProbe) -> None:
                self._outgoing = outgoing
                self._uow = uow

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                trace.append(f'handler:{uows.index(self._uow) + 1}')
                self._outgoing.publish(_CascadeEvent(value=request.value))

        class _CascadeHandler(EventHandler[_CascadeEvent]):
            def __init__(self, uow: IUnitOfWork) -> None:
                self._uow = uow

            @override
            async def handle(self, event: _CascadeEvent, /) -> None:
                trace.append(f'cascade:{uows.index(self._uow) + 1}')

        config = MessagingConfig(
            endpoints=[local_queue('local://cascade', mode=EndpointMode.INLINE)],
            routing=[route(_CascadeEvent).to('local://cascade')],
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_OuterHandler).bind(_CascadeHandler)],
            providers=[
                provider(provide_uow, provided_type=IUnitOfWork),
                provider(provide_probe),
            ],
        ) as app:
            executor = await _make_executor(app, await app.container.get(ErrorPolicyEvaluator))
            result = await executor.execute(make_envelope(_FailingCommand(value='cascade')), _OuterHandler)

        assert result.outcome is ExecutionOutcome.SUCCESS
        assert trace == ['handler:1', 'commit:1', 'origin-exit', 'cascade:2', 'commit:2']
        assert uows[0] is not uows[1]

    @staticmethod
    async def test_fresh_flush_teardown_failure_is_after_commit_without_handler_retry() -> None:
        calls: list[str] = []
        teardown_error = RuntimeError('fresh flush scope teardown failed')

        class _OuterHandler(RequestHandler[_FailingCommand, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                calls.append('outer')
                self._outgoing.publish(_CascadeEvent(value=request.value))

        class _CascadeHandler(EventHandler[_CascadeEvent]):
            def __init__(self, probe: _ScopeProbe) -> None:
                self._probe = probe

            @override
            async def handle(self, event: _CascadeEvent, /) -> None:
                calls.append('cascade')

        async def provide_probe() -> AsyncIterator[_ScopeProbe]:
            await anyio.lowlevel.checkpoint()
            yield _ScopeProbe()
            raise teardown_error

        uow = RecordingUoW()
        config = MessagingConfig(
            endpoints=[local_queue('local://cascade', mode=EndpointMode.INLINE)],
            routing=[route(_CascadeEvent).to('local://cascade')],
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=2).then_discard(),),
            ),
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_OuterHandler).bind(_CascadeHandler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                provider(provide_probe),
            ],
        ) as app:
            executor = await _make_executor(app, await app.container.get(ErrorPolicyEvaluator))
            with pytest.raises(TransactionExecutionError) as raised:
                await executor.execute(make_envelope(_FailingCommand(value='cascade')), _OuterHandler)

        assert raised.value.kind is TransactionFailureKind.AFTER_COMMIT
        assert uow.rollback_count == 0
        assert calls == ['outer', 'cascade']


class TestEndpointExecutorDeadLetter:
    @staticmethod
    @pytest.mark.parametrize('mode', [EndpointMode.INLINE, EndpointMode.BUFFERED])
    async def test_policy_only_dead_letter_capability_persists_through_both_factory_paths(
        mode: EndpointMode,
    ) -> None:
        _PolicyDeadLetterHandler.calls = 0
        config = MessagingConfig(
            endpoints=[local_queue('local://policy-dlq', mode=mode)],
            routing=[route(_PolicyDeadLetterEvent).to('local://policy-dlq')],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            ),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), MemoryBackend.register()],
                extensions=[MessagingExtension().bind(_PolicyDeadLetterHandler)],
            ) as app,
            app.container() as scope,
        ):
            bus = await scope.get(IMessageBus)
            await bus.publish(_PolicyDeadLetterEvent(value=mode.value))

            entries: list[DeadLetterEntry] = []
            with anyio.fail_after(5):
                while not entries:
                    async with app.container() as inspection_scope:
                        store = await inspection_scope.get(IDeadLetterStore)
                        entries = list(await store.fetch())
                    if not entries:
                        await anyio.lowlevel.checkpoint()

        assert _PolicyDeadLetterHandler.calls == 1
        assert len(entries) == 1
        assert entries[0].error_message == f'policy dead letter: {mode.value}'
        assert entries[0].retry_count == 1

    @staticmethod
    async def test_dead_letter_policy_writes_entry_with_error_details() -> None:
        handler, _ = _make_always_fail_handler()
        dl_store = RecordingDeadLetterStore()
        uow = RecordingUoW()

        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='to-dlq'))
            outcome = (await executor.execute(envelope, handler)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED
        assert len(dl_store.entries) == 1
        assert 'permanent failure' in dl_store.entries[0].error_message
        assert dl_store.entries[0].retry_count == 1
        assert dl_store.entries[0].destination == 'test://q'

    @staticmethod
    async def test_dead_letter_entry_carries_envelope_wire_name() -> None:
        handler, _ = _make_always_fail_handler()
        dl_store = RecordingDeadLetterStore()
        uow = RecordingUoW()

        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            # The wire name on the envelope differs from the payload type's FQN — e.g. a message_identity
            # alias that changed since publish. The DLQ entry must carry the authoritative envelope name.
            envelope = replace(make_envelope(_FailingCommand(value='to-dlq')), message_type='wire.RenamedAlias')
            outcome = (await executor.execute(envelope, handler)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED
        assert dl_store.entries[0].message_type == 'wire.RenamedAlias'

    @staticmethod
    async def test_dead_letter_save_failure_is_logged_after_rollback(caplog: pytest.LogCaptureFixture) -> None:
        handler, _ = _make_always_fail_handler()
        uow = RecordingUoW()
        dead_letters = FailingDeadLetterStore()

        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.executor'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(handler)],
                providers=[
                    object_(uow, provided_type=IUnitOfWork),
                    object_(dead_letters, provided_type=IDeadLetterStore),
                    object_(_durability(dead_letters, uow), provided_type=IDurabilityStore),
                ],
            ) as app:
                envelope = make_envelope(_FailingCommand(value='dlq-fail'))
                outcome = (
                    await materialize_standalone_dead_letter(
                        app.container,
                        envelope=envelope,
                        endpoint_uri='test://q',
                        intent=_dead_letter_intent(),
                    )
                ).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTER_FAILED
        assert uow.rollback_count == 1
        assert 'Failed to write dead letter entry' in caplog.text

    @staticmethod
    async def test_dead_letter_write_failure_returns_failed_outcome() -> None:
        handler, _ = _make_always_fail_handler()
        dead_letters = FailingDeadLetterStore()
        uow = RecordingUoW()
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dead_letters, provided_type=IDeadLetterStore),
                object_(_durability(dead_letters, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            envelope = make_envelope(_FailingCommand(value='dlq-fail'))
            outcome = (
                await materialize_standalone_dead_letter(
                    app.container,
                    envelope=envelope,
                    endpoint_uri='test://q',
                    intent=_dead_letter_intent(),
                )
            ).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTER_FAILED  # ERR-2: failed DLQ write keeps the durable row

    @staticmethod
    async def test_dead_letter_commit_failure_rolls_back_before_failed_outcome() -> None:
        handler, _ = _make_always_fail_handler()
        actions: list[str] = []
        commit_error = RuntimeError('commit failed')
        uow = _ActionRecordingUoW(actions, commit_error=commit_error)
        dl_store = _ActionRecordingDeadLetterStore(actions)
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            envelope = make_envelope(_FailingCommand(value='dlq-commit-fail'))
            outcome = (
                await materialize_standalone_dead_letter(
                    app.container,
                    envelope=envelope,
                    endpoint_uri='test://q',
                    intent=_dead_letter_intent(),
                )
            ).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTER_FAILED
        assert actions == ['save', 'commit', 'rollback']

    @staticmethod
    async def test_dead_letter_rollback_failure_escapes_instead_of_returning_failed_outcome() -> None:
        handler, _ = _make_always_fail_handler()
        actions: list[str] = []
        rollback_error = RuntimeError('rollback failed')
        uow = _ActionRecordingUoW(
            actions,
            commit_error=RuntimeError('commit failed'),
            rollback_error=rollback_error,
        )
        dl_store = _ActionRecordingDeadLetterStore(actions)
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            envelope = make_envelope(_FailingCommand(value='dlq-rollback-fail'))
            with pytest.raises(TransactionExecutionError) as caught:
                await materialize_standalone_dead_letter(
                    app.container,
                    envelope=envelope,
                    endpoint_uri='test://q',
                    intent=_dead_letter_intent(),
                )

        assert caught.value.kind is TransactionFailureKind.ROLLBACK_FAILED
        assert caught.value.error is rollback_error
        assert actions == ['save', 'commit', 'rollback']

    @staticmethod
    async def test_dead_letter_commit_cancellation_completes_rollback_and_remains_cancellation() -> None:
        handler, _ = _make_always_fail_handler()
        actions: list[str] = []
        cancel_scope = anyio.CancelScope()
        uow = _ActionRecordingUoW(actions, cancel_scope=cancel_scope)
        dl_store = _ActionRecordingDeadLetterStore(actions)
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            envelope = make_envelope(_FailingCommand(value='dlq-commit-cancelled'))
            with cancel_scope:
                await materialize_standalone_dead_letter(
                    app.container,
                    envelope=envelope,
                    endpoint_uri='test://q',
                    intent=_dead_letter_intent(),
                )

        assert cancel_scope.cancelled_caught
        assert actions == ['save', 'commit', 'rollback']


async def test_on_result_called_with_success() -> None:
    handler, _ = _make_fail_n_times_handler(fail_count=0)
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert result is ExecutionOutcome.SUCCESS
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]


async def test_on_result_called_with_failure_no_policy() -> None:
    handler, _ = _make_always_fail_handler()
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert result is ExecutionOutcome.FAILED_NO_POLICY
    assert len(recorded) == 1
    outcome, exc = recorded[0]
    assert outcome is ExecutionOutcome.FAILED_NO_POLICY
    assert isinstance(exc, ValueError)


async def test_execute_without_on_result_is_unchanged() -> None:
    handler, _ = _make_always_fail_handler()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler)).outcome

    assert result is ExecutionOutcome.FAILED_NO_POLICY


async def test_on_result_fired_once_across_retries() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=3))
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='retry'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert len(calls) == 2  # one failure + one success
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]  # fired once with terminal outcome, not per-retry
    assert result is ExecutionOutcome.SUCCESS


class TestHandlerExecutionTimeout:
    @staticmethod
    async def test_handler_execution_timeout_overrun_dead_letters() -> None:
        dl_store = RecordingDeadLetterStore()
        uow = RecordingUoW()
        blocked = anyio.Event()  # never set: the handler stalls until the deadline cancels it

        class _BlockingHandler(RequestHandler[_FailingCommand, None]):
            execution_timeout = timedelta(seconds=0.01)

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                await blocked.wait()

        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )
        observer, recorded = _make_observer()

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
            providers=[
                object_(uow, provided_type=IUnitOfWork),
                object_(dl_store, provided_type=IDeadLetterStore),
                object_(_durability(dl_store, uow), provided_type=IDurabilityStore),
            ],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='slow'))
            outcome = (await executor.execute(envelope, _BlockingHandler, on_result=observer)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED  # HandlerTimeoutError flows through error_policies
        recorded_outcome, exc = recorded[0]
        assert recorded_outcome is ExecutionOutcome.DEAD_LETTERED
        assert isinstance(exc, HandlerTimeoutError)
        assert len(dl_store.entries) == 1

    @staticmethod
    async def test_default_execution_timeout_applies_when_handler_unset() -> None:
        blocked = anyio.Event()  # never set

        class _BlockingHandler(RequestHandler[_FailingCommand, None]):
            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                await blocked.wait()

        observer, recorded = _make_observer()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
        ) as app:
            invoker = await app.container.get(HandlerPipelineInvoker)
            # execution_timeout unset (MISSING) → executor-level default fires.
            executor = EndpointExecutor(
                container=app.container,
                evaluator=NOOP_EVALUATOR,
                endpoint_uri='test://q',
                invoker=invoker,
                observers=MessageObservers([]),
                default_execution_timeout=timedelta(seconds=0.01),
            )
            envelope = make_envelope(_FailingCommand(value='slow'))
            outcome = (await executor.execute(envelope, _BlockingHandler, on_result=observer)).outcome

        assert outcome is ExecutionOutcome.FAILED_NO_POLICY
        recorded_outcome, exc = recorded[0]
        assert recorded_outcome is ExecutionOutcome.FAILED_NO_POLICY
        assert isinstance(exc, HandlerTimeoutError)

    @staticmethod
    async def test_execution_timeout_none_opts_out_of_default() -> None:
        class _UnboundedHandler(RequestHandler[_FailingCommand, None]):
            execution_timeout = None

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                # default_execution_timeout below is already-expired (0s): if None were wrongly treated as
                # "inherit," the very first checkpoint would trip the deadline. No real sleep needed —
                # move_on_after(0) reliably cancels at the next checkpoint (verified empirically).
                for _ in range(10):
                    await anyio.lowlevel.checkpoint()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_UnboundedHandler)],
        ) as app:
            invoker = await app.container.get(HandlerPipelineInvoker)
            executor = EndpointExecutor(
                container=app.container,
                evaluator=NOOP_EVALUATOR,
                endpoint_uri='test://q',
                invoker=invoker,
                observers=MessageObservers([]),
                default_execution_timeout=timedelta(),
            )
            envelope = make_envelope(_FailingCommand(value='slow-but-allowed'))
            outcome = (await executor.execute(envelope, _UnboundedHandler)).outcome

        assert outcome is ExecutionOutcome.SUCCESS


@dataclass(frozen=True, slots=True)
class _CheckpointCommand(IRequest[None]):
    ref: str = ''


class _CheckpointHandler(RequestHandler[_CheckpointCommand, None]):
    # execution_timeout intentionally unset (MISSING) → inherits the executor's default deadline.
    completed: ClassVar[list[str]] = []

    @override
    async def handle(self, request: _CheckpointCommand, /) -> None:
        await anyio.lowlevel.checkpoint()
        _CheckpointHandler.completed.append(request.ref)


async def test_factory_gives_live_and_recovery_uris_identical_deadline_and_clock() -> None:
    config = MessagingConfig(endpoint_defaults=EndpointDefaults(execution_timeout=timedelta()))
    async with create_test_app(
        imports=[MessagingModule.register(config)],
        extensions=[MessagingExtension().bind(_CheckpointHandler)],
    ) as app:
        factory = await app.container.get(EndpointExecutorFactory)
        live = factory.for_uri('local://orders')
        recovery = factory.for_uri('rabbitmq://orders')

        past = datetime.now(tz=UTC) - timedelta(seconds=1)
        for executor in (live, recovery):
            expired = make_envelope(_CheckpointCommand(ref='expired'), expires_at=past)
            discarded = (await executor.execute(expired, _CheckpointHandler)).outcome
            assert discarded is ExecutionOutcome.DISCARDED  # clock: past expires_at → discard on both URIs

            fresh = make_envelope(_CheckpointCommand(ref='fresh'))
            deadline = (await executor.execute(fresh, _CheckpointHandler)).outcome
            assert deadline is ExecutionOutcome.FAILED_NO_POLICY  # same 0s default deadline fires on both URIs

        assert factory.for_uri('local://orders') is live  # memoization: cache-hit returns the same instance
        assert _CheckpointHandler.completed == []  # the deadline cancelled the handler before it recorded


@dataclass(frozen=True, slots=True)
class _Ping(IRequest[None]):
    ref: str = ''


class _PingHandler(RequestHandler[_Ping, None]):
    @override
    async def handle(self, request: _Ping, /) -> None:
        return


class _Spy(IMessageObserver):
    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self.events.append('executing')

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self.events.append(f'executed:{outcome.value}:{duration.total_seconds() * 1000:.0f}')


class _DestinationSpy(IMessageObserver):
    def __init__(self) -> None:
        self.destinations: list[str] = []

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self.destinations.append(destination)

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self.destinations.append(destination)


@asynccontextmanager
async def _executor(
    *,
    observers: MessageObservers,
    monotonic: Callable[[], float] = time.perf_counter,
) -> AsyncGenerator[EndpointExecutor]:
    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_PingHandler)],
    ) as app:
        invoker = await app.container.get(HandlerPipelineInvoker)
        yield EndpointExecutor(
            container=app.container,
            evaluator=NOOP_EVALUATOR,
            endpoint_uri='test://q',
            invoker=invoker,
            observers=observers,
            monotonic=monotonic,
        )


async def test_executing_then_executed_fire_with_outcome_and_duration() -> None:
    spy = _Spy()
    reads = iter([0.0, 5.0])  # exactly two reads on the SUCCESS path -> 5000ms; a 3rd read raises StopIteration
    async with _executor(observers=MessageObservers([spy]), monotonic=lambda: next(reads)) as ex:
        await ex.execute(make_envelope(_Ping(ref='r')), _PingHandler)
    assert spy.events == ['executing', 'executed:SUCCESS:5000']


async def test_expired_fires_executed_only_no_executing() -> None:
    spy = _Spy()
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    async with _executor(observers=MessageObservers([spy])) as ex:
        await ex.execute(make_envelope(_Ping(), expires_at=past), _PingHandler)
    assert spy.events == ['executed:DISCARDED:0']


async def test_execution_hooks_receive_endpoint_uri() -> None:
    spy = _DestinationSpy()
    async with _executor(observers=MessageObservers([spy])) as ex:
        await ex.execute(make_envelope(_Ping(ref='r')), _PingHandler)
    assert spy.destinations == ['test://q', 'test://q']


async def test_expired_discard_reports_endpoint_uri_on_executed() -> None:
    spy = _DestinationSpy()
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    async with _executor(observers=MessageObservers([spy])) as ex:
        await ex.execute(make_envelope(_Ping(ref='r'), expires_at=past), _PingHandler)
    assert spy.destinations == ['test://q']  # executed only — no executing on the expired path


async def test_expired_fires_executed_before_on_result() -> None:
    order: list[str] = []

    class _OrderSpy(IMessageObserver):
        @override
        async def on_executed(
            self,
            envelope: MessageEnvelope[Any],
            destination: str,
            handler_type: HandlerType,
            outcome: ExecutionOutcome,
            exc: Exception | None,
            duration: timedelta,
        ) -> None:
            order.append('observed')

    async def _recording_on_result(_outcome: ExecutionOutcome, _exc: Exception | None) -> None:  # noqa: RUF029
        order.append('control')

    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    async with _executor(observers=MessageObservers([_OrderSpy()])) as ex:
        await ex.execute(make_envelope(_Ping(), expires_at=past), _PingHandler, on_result=_recording_on_result)
    assert order == ['observed', 'control']


async def test_observer_raise_does_not_change_result() -> None:
    class _BadObs(IMessageObserver):
        @override
        async def on_executed(
            self,
            envelope: MessageEnvelope[Any],
            destination: str,
            handler_type: HandlerType,
            outcome: ExecutionOutcome,
            exc: Exception | None,
            duration: timedelta,
        ) -> None:
            msg = 'observer down'
            raise RuntimeError(msg)

    async with _executor(observers=MessageObservers([_BadObs()])) as ex:
        result = await ex.execute(make_envelope(_Ping()), _PingHandler)
    assert result.outcome is ExecutionOutcome.SUCCESS  # observer fault swallowed, result intact


async def test_executed_fires_before_on_result_even_when_on_result_raises() -> None:
    order: list[str] = []

    class _OrderSpy(IMessageObserver):
        @override
        async def on_executed(
            self,
            envelope: MessageEnvelope[Any],
            destination: str,
            handler_type: HandlerType,
            outcome: ExecutionOutcome,
            exc: Exception | None,
            duration: timedelta,
        ) -> None:
            order.append('observed')

    async def _raising_on_result(_outcome: ExecutionOutcome, _exc: Exception | None) -> None:  # noqa: RUF029
        order.append('control')
        msg = 'cb boom'
        raise RuntimeError(msg)

    async with _executor(observers=MessageObservers([_OrderSpy()])) as ex:
        with pytest.raises(RuntimeError):
            await ex.execute(make_envelope(_Ping()), _PingHandler, on_result=_raising_on_result)
    assert order == ['observed', 'control']  # observability recorded before the control hook
