from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Any, Generic, Never, TypeAlias, TypeVar, assert_never

import anyio
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import (
    Abort,
    Aborted,
    Commit,
    Committed,
    RolledBack,
    TransactionDecision,
    execute_in_uow_scope,
)
from waku.messaging._internal.outbox_cascading import DeferredCascadeFlusher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging.behaviors.transactional import decide_transaction
from waku.messaging.context import message_context_scope
from waku.messaging.endpoints._internal.aspects import resolve_override
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.executor import FailureContext
from waku.messaging.errors.policy import RetryAction
from waku.messaging.exceptions import HandlerTimeoutError
from waku.messaging.outgoing import IOutgoingMessagesFrames

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import CallNext
    from waku.messaging.errors.executor import ErrorPolicyEvaluator, PolicyOutcome
    from waku.messaging.observability.observer import MessageObservers, ObserverPlan
    from waku.messaging.outgoing import DeferredCascadeEffects
    from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker

logger = logging.getLogger(__name__)

_ResultT_co = TypeVar('_ResultT_co', covariant=True)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    outcome: ExecutionOutcome
    pause_duration: timedelta | None = None
    requeue_limit: int | None = None


@unique
class TerminalIntentKind(StrEnum):
    SUCCESS = 'SUCCESS'
    FAILED_NO_POLICY = 'FAILED_NO_POLICY'
    DEAD_LETTER = 'DEAD_LETTER'
    DISCARD = 'DISCARD'
    REQUEUE = 'REQUEUE'
    PAUSE = 'PAUSE'


@dataclass(frozen=True, slots=True)
class TerminalIntent:
    kind: TerminalIntentKind
    error: Exception | None = None
    attempt: int = 0
    duration: timedelta = timedelta()
    pause_duration: timedelta | None = None
    requeue_limit: int | None = None


def outcome_from_intent(intent: TerminalIntent) -> ExecutionOutcome:
    """Map a materialized terminal intent to its execution outcome.

    Shared by the buffered finalize path and the redelivery default sink. DEAD_LETTER needs an owner
    transaction and REQUEUE/PAUSE must be redelivered before materialization, so both raise here rather
    than map — the ``assert_never`` turns a new ``TerminalIntentKind`` into a type error, not a runtime gap.

    Raises:
        RuntimeError: The intent is DEAD_LETTER or a deferred REQUEUE/PAUSE kind — neither is materializable here.
    """
    match intent.kind:
        case TerminalIntentKind.SUCCESS:
            return ExecutionOutcome.SUCCESS
        case TerminalIntentKind.FAILED_NO_POLICY:
            return ExecutionOutcome.FAILED_NO_POLICY
        case TerminalIntentKind.DISCARD:
            return ExecutionOutcome.DISCARDED
        case TerminalIntentKind.DEAD_LETTER:
            msg = 'dead-letter intent requires an owner transaction'
            raise RuntimeError(msg)
        case TerminalIntentKind.REQUEUE | TerminalIntentKind.PAUSE:
            msg = 'deferred terminal intent must be redelivered before materialization'
            raise RuntimeError(msg)
        case _ as unreachable:  # pragma: no cover
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class _DetachedInvocation(Generic[_ResultT_co]):
    value: _ResultT_co
    cascades: DeferredCascadeEffects


@dataclass(frozen=True, slots=True)
class _HandlerAttemptFailure:
    error: Exception


ResultObserver: TypeAlias = 'Callable[[ExecutionOutcome, Exception | None], Awaitable[None]]'
_ExecutionWrapper: TypeAlias = 'Callable[[CallNext[Any]], Awaitable[Any]]'


async def noop_result_observer(outcome: ExecutionOutcome, exc: Exception | None) -> None:
    """Ignore an endpoint execution result."""


class IEndpointExecution(ABC):
    @abstractmethod
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent: ...

    @abstractmethod
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None: ...


class EndpointExecution(IEndpointExecution):
    """Signal-preserving endpoint execution used by internal transaction owners."""

    __slots__ = (
        '_container',
        '_default_execution_timeout',
        '_endpoint_uri',
        '_evaluator',
        '_invoker',
        '_monotonic',
        '_now',
        '_observers',
        '_sleep',
    )

    def __init__(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
        self,
        *,
        container: AsyncContainer,
        evaluator: ErrorPolicyEvaluator,
        endpoint_uri: str,
        invoker: HandlerPipelineInvoker,
        observers: MessageObservers,
        default_execution_timeout: timedelta | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        now: Now = utc_now,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._container = container
        self._evaluator = evaluator
        self._endpoint_uri = endpoint_uri
        self._invoker = invoker
        self._observers = observers
        self._default_execution_timeout = default_execution_timeout
        self._sleep = sleep
        self._now = now
        self._monotonic = monotonic

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        if self._is_expired(envelope):
            logger.info('Discarding expired message_id=%s (expires_at=%s)', envelope.message_id, envelope.expires_at)
            return TerminalIntent(TerminalIntentKind.DISCARD)
        await self._observers.executing(envelope, self._endpoint_uri, handler_type)
        start = self._monotonic()
        intent = await self._run_attempts(envelope, handler_type)
        return replace(intent, duration=timedelta(seconds=self._monotonic() - start))

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        await self._observers.executed(
            envelope,
            self._endpoint_uri,
            handler_type,
            result.outcome,
            intent.error,
            intent.duration,
        )
        await on_result(result.outcome, intent.error)

    def _is_expired(self, envelope: MessageEnvelope[Any]) -> bool:
        return envelope.expires_at is not None and envelope.expires_at <= self._now()

    async def _run_attempts(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        attempt = 0
        while True:
            attempt += 1
            failure = await self._dispatch_in_scope(envelope, handler_type)
            if failure is None:
                return TerminalIntent(TerminalIntentKind.SUCCESS, attempt=attempt)

            exc = failure.error
            outcome = self._evaluate(envelope, handler_type, exc, attempt)
            if outcome is None:
                logger.error('%s failed: message_id=%s', handler_type.__name__, envelope.message_id, exc_info=exc)
                return TerminalIntent(TerminalIntentKind.FAILED_NO_POLICY, error=exc, attempt=attempt)
            intent = await self._handle_failure(outcome, envelope, exc, attempt)
            if intent is None:
                continue
            return intent

    def _resolve_timeout(self, handler_type: HandlerType) -> timedelta | None:
        return resolve_override(handler_type.execution_timeout, self._default_execution_timeout)

    async def _dispatch_in_scope(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> _HandlerAttemptFailure | None:
        timeout = self._resolve_timeout(handler_type)

        async def execute_with_timeout(call_next: CallNext[Any]) -> Any:
            with anyio.move_on_after(timeout.total_seconds() if timeout is not None else None) as cancel_scope:
                result = await call_next()
            if cancel_scope.cancelled_caught:
                raise HandlerTimeoutError(envelope.message_id, timeout)
            return result

        if self._invoker.has_transaction(handler_type):
            return await self._dispatch_transactional(envelope, handler_type, execute_with_timeout)
        return await self._dispatch_direct(envelope, handler_type, execute_with_timeout)

    async def _dispatch_transactional(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        execute_with_timeout: _ExecutionWrapper,
    ) -> _HandlerAttemptFailure | None:
        async def invoke_transactional(
            scope: AsyncContainer,
        ) -> TransactionDecision[_DetachedInvocation[Any], Never]:
            depth = await scope.get(TransactionDepth)

            async def invoke() -> Any:
                with message_context_scope(envelope):
                    return await self._invoker.invoke(
                        scope,
                        envelope.payload,
                        handler_type,
                        execution_wrapper=execute_with_timeout,
                    )

            decision = await decide_transaction(depth, invoke)
            if isinstance(decision, Abort):
                return decision
            if not isinstance(decision, Commit):
                assert_never(decision.value)
            outgoing = await scope.get(IOutgoingMessagesFrames)
            return Commit(_DetachedInvocation(decision.value, outgoing.detach_deferred()))

        async def flush_committed(invocation: _DetachedInvocation[Any]) -> None:
            await self._flush_batch(invocation.cascades)

        result = await execute_in_uow_scope(
            self._container,
            invoke_transactional,
            after_commit=flush_committed,
        )
        if isinstance(result, Committed):
            return None
        if isinstance(result, Aborted):
            return _HandlerAttemptFailure(result.error)
        if isinstance(result, RolledBack):
            assert_never(result.value)
        assert_never(result)

    async def _dispatch_direct(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        execute_with_timeout: _ExecutionWrapper,
    ) -> _HandlerAttemptFailure | None:
        async with self._container() as scope:
            try:
                with message_context_scope(envelope):
                    await self._invoker.invoke(
                        scope,
                        envelope.payload,
                        handler_type,
                        execution_wrapper=execute_with_timeout,
                    )
            except Exception as error:  # noqa: BLE001 -- only handler failures become retryable attempt evidence
                outgoing = await scope.get(IOutgoingMessagesFrames)
                outgoing.detach_deferred()
                return _HandlerAttemptFailure(error)
            else:
                outgoing = await scope.get(IOutgoingMessagesFrames)
                batch = outgoing.detach_deferred()

        await self._flush_batch(batch)
        return None

    async def _flush_batch(self, effects: DeferredCascadeEffects, /) -> None:
        if not effects:
            return
        async with self._container() as fresh_scope:
            flusher = await fresh_scope.get(DeferredCascadeFlusher)
            await flusher.flush(effects)

    def _evaluate(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        exc: Exception,
        attempt: int,
    ) -> PolicyOutcome | None:
        return self._evaluator.evaluate(
            FailureContext(
                message_type=type(envelope.payload),
                handler_type=handler_type,
                exc=exc,
                attempt=attempt,
            )
        )

    async def _handle_failure(
        self,
        outcome: PolicyOutcome,
        envelope: MessageEnvelope[Any],
        exc: Exception,
        attempt: int,
    ) -> TerminalIntent | None:
        match outcome.action:
            case RetryAction.DEAD_LETTER:
                return TerminalIntent(TerminalIntentKind.DEAD_LETTER, error=exc, attempt=attempt)
            case RetryAction.DISCARD:
                logger.info('Discarded message_id=%s after %d attempt(s)', envelope.message_id, attempt)
                return TerminalIntent(TerminalIntentKind.DISCARD, error=exc, attempt=attempt)
            case RetryAction.RETRY | RetryAction.RETRY_WITH_BACKOFF:
                logger.info(
                    'Retrying message_id=%s (attempt %d, delay=%.2fs)',
                    envelope.message_id,
                    attempt,
                    outcome.retry_delay.total_seconds() if outcome.retry_delay is not None else 0,
                )
                if outcome.retry_delay is not None:
                    await self._sleep(outcome.retry_delay.total_seconds())
                return None
            case RetryAction.REQUEUE:
                logger.info('Requeuing message_id=%s after %d attempt(s)', envelope.message_id, attempt)
                return TerminalIntent(
                    TerminalIntentKind.REQUEUE,
                    error=exc,
                    attempt=attempt,
                    requeue_limit=outcome.requeue_limit,
                )
            case RetryAction.PAUSE:
                logger.warning(
                    'Pausing listener after message_id=%s failed (%d attempt(s))', envelope.message_id, attempt
                )
                return TerminalIntent(
                    TerminalIntentKind.PAUSE,
                    error=exc,
                    attempt=attempt,
                    pause_duration=outcome.pause_duration,
                    requeue_limit=outcome.requeue_limit,
                )
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)


class EndpointExecutionFactory:
    """Build and memoize signal-preserving endpoint executions for internal owners."""

    __slots__ = ('_cache', '_container', '_default_execution_timeout', '_evaluator', '_invoker', '_now', '_plan')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        evaluator: ErrorPolicyEvaluator,
        invoker: HandlerPipelineInvoker,
        plan: ObserverPlan,
        default_execution_timeout: timedelta | None,
        now: Now,
    ) -> None:
        self._container = container
        self._evaluator = evaluator
        self._invoker = invoker
        self._plan = plan
        self._default_execution_timeout = default_execution_timeout
        self._now = now
        self._cache: dict[str, IEndpointExecution] = {}

    def for_uri(self, endpoint_uri: str) -> IEndpointExecution:
        execution = self._cache.get(endpoint_uri)
        if execution is None:
            execution = EndpointExecution(
                container=self._container,
                evaluator=self._evaluator,
                endpoint_uri=endpoint_uri,
                invoker=self._invoker,
                observers=self._plan.for_endpoint(endpoint_uri),
                default_execution_timeout=self._default_execution_timeout,
                now=self._now,
            )
            self._cache[endpoint_uri] = execution
        return execution
