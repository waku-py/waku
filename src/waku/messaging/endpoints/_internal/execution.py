from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final, Generic, Never, TypeAlias, TypeVar, assert_never

import anyio
from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.sentinel import MISSING
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
from waku.messaging.durability import IDurabilityStore
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.errors.executor import FailureContext
from waku.messaging.errors.policy import RetryAction
from waku.messaging.exceptions import HandlerTimeoutError
from waku.messaging.outgoing import IOutgoingMessagesFrames
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import CallNext
    from waku.messaging.errors.executor import ErrorPolicyEvaluator, PolicyOutcome
    from waku.messaging.observability.observer import MessageObservers, ObserverPlan
    from waku.messaging.outgoing import DeferredCascadeBatch
    from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker

logger = logging.getLogger(__name__)

_ResultT_co = TypeVar('_ResultT_co', covariant=True)


DEFERRED_TERMINAL_OUTCOMES: Final[frozenset[ExecutionOutcome]] = frozenset({
    ExecutionOutcome.REQUEUED,
    ExecutionOutcome.PAUSED,
})


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    outcome: ExecutionOutcome
    pause_duration: timedelta | None = None
    requeue_limit: int | None = None


@dataclass(frozen=True, slots=True)
class _DetachedInvocation(Generic[_ResultT_co]):
    value: _ResultT_co
    cascades: DeferredCascadeBatch


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
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> ExecutionResult: ...


class IEndpointWorkerExecution(IEndpointExecution):
    @abstractmethod
    async def write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> bool: ...


class EndpointExecution(IEndpointWorkerExecution):
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
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> ExecutionResult:
        if self._is_expired(envelope):
            logger.info('Discarding expired message_id=%s (expires_at=%s)', envelope.message_id, envelope.expires_at)
            result = ExecutionResult(ExecutionOutcome.DISCARDED)
            await self._observers.executed(
                envelope, self._endpoint_uri, handler_type, result.outcome, None, timedelta()
            )
            await on_result(result.outcome, None)
            return result
        await self._observers.executing(envelope, self._endpoint_uri, handler_type)
        start = self._monotonic()
        result, exc = await self._run_attempts(envelope, handler_type)
        duration = timedelta(seconds=self._monotonic() - start)
        await self._observers.executed(envelope, self._endpoint_uri, handler_type, result.outcome, exc, duration)
        await on_result(result.outcome, exc)
        return result

    def _is_expired(self, envelope: MessageEnvelope[Any]) -> bool:
        return envelope.expires_at is not None and envelope.expires_at <= self._now()

    async def _run_attempts(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> tuple[ExecutionResult, Exception | None]:
        attempt = 0
        while True:
            attempt += 1
            failure = await self._dispatch_in_scope(envelope, handler_type)
            if failure is None:
                return ExecutionResult(ExecutionOutcome.SUCCESS), None

            exc = failure.error
            outcome = self._evaluate(envelope, handler_type, exc, attempt)
            if outcome is None:
                logger.error('%s failed: message_id=%s', handler_type.__name__, envelope.message_id, exc_info=exc)
                return ExecutionResult(ExecutionOutcome.FAILED_NO_POLICY), exc
            result = await self._handle_failure(outcome, envelope, exc, attempt)
            if result is None:
                continue
            return result, exc

    def _resolve_timeout(self, handler_type: HandlerType) -> timedelta | None:
        value = handler_type.execution_timeout
        return self._default_execution_timeout if value is MISSING else value  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows

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

    async def _flush_batch(self, batch: DeferredCascadeBatch, /) -> None:
        if not batch:
            return
        async with self._container() as fresh_scope:
            flusher = await fresh_scope.get(DeferredCascadeFlusher)
            await flusher.flush(batch)

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
    ) -> ExecutionResult | None:
        match outcome.action:
            case RetryAction.DEAD_LETTER:
                logger.warning('Moving message_id=%s to dead letter after %d attempt(s)', envelope.message_id, attempt)
                persisted = await self.write_dead_letter(envelope, exc, attempt)
                dlq_outcome = ExecutionOutcome.DEAD_LETTERED if persisted else ExecutionOutcome.DEAD_LETTER_FAILED
                return ExecutionResult(dlq_outcome)
            case RetryAction.DISCARD:
                logger.info('Discarded message_id=%s after %d attempt(s)', envelope.message_id, attempt)
                return ExecutionResult(ExecutionOutcome.DISCARDED)
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
                return ExecutionResult(ExecutionOutcome.REQUEUED, requeue_limit=outcome.requeue_limit)
            case RetryAction.PAUSE:
                logger.warning(
                    'Pausing listener after message_id=%s failed (%d attempt(s))', envelope.message_id, attempt
                )
                return ExecutionResult(
                    ExecutionOutcome.PAUSED, outcome.pause_duration, requeue_limit=outcome.requeue_limit
                )
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    @override
    async def write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> bool:
        async def save(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            durability = await scope.get(IDurabilityStore)
            codec = await scope.get(PayloadCodec)
            entry = DeadLetterEntry.from_failure(
                message_type=envelope.message_type,
                payload=encode_payload(envelope, codec),
                destination=self._endpoint_uri,
                destination_kind=DeadLetterDestinationKind.ENDPOINT,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                exc=exc,
                attempt=attempt,
                message_id=envelope.message_id,
                metadata=encode_metadata(envelope),
                group_id=envelope.group_id,
            )
            await durability.dead_letters.save(entry)
            return Commit(value=True)

        result = await execute_in_uow_scope(self._container, save)
        if isinstance(result, Committed):
            return result.value
        if isinstance(result, Aborted):
            logger.error(
                'Failed to write dead letter entry for message_id=%s',
                envelope.message_id,
                exc_info=result.error,
            )
            return False
        if isinstance(result, RolledBack):
            assert_never(result.value)
        assert_never(result)


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
        self._cache: dict[str, IEndpointWorkerExecution] = {}

    def for_uri(self, endpoint_uri: str) -> IEndpointWorkerExecution:
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
