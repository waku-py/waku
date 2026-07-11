from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeAlias, assert_never

import anyio
from dishka.exceptions import NoFactoryError

from waku._internal.clock import utc_now
from waku._internal.sentinel import MISSING
from waku.messaging.context import message_context_scope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.executor import FailureContext
from waku.messaging.errors.policy import RetryAction
from waku.messaging.exceptions import HandlerTimeoutError
from waku.messaging.transport.decomposition import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork, NoOpUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.executor import ErrorPolicyEvaluator, PolicyOutcome
    from waku.messaging.observability.observer import MessageObservers, ObserverPlan
    from waku.messaging.pipeline.invoker import HandlerPipelineInvoker

__all__ = [
    'EndpointExecutor',
    'EndpointExecutorFactory',
    'ExecutionOutcome',
    'ExecutionResult',
]

logger = logging.getLogger(__name__)


@enum.unique
class ExecutionOutcome(enum.Enum):
    SUCCESS = 'SUCCESS'
    DEAD_LETTERED = 'DEAD_LETTERED'
    DEAD_LETTER_FAILED = 'DEAD_LETTER_FAILED'  # DLQ write failed; durable row survives for recovery
    DISCARDED = 'DISCARDED'
    FAILED_NO_POLICY = 'FAILED_NO_POLICY'  # failed with no recovery: endpoint path = no policy matched; invoke path
    # = policies never consulted, exception propagates to the caller
    REQUEUED = 'REQUEUED'  # deferred-terminal: endpoint re-delivers
    PAUSED = 'PAUSED'  # deferred-terminal: re-deliver + pause listener for policy's pause_duration


# Outcomes the endpoint re-delivers instead of finalizing.
DEFERRED_TERMINAL_OUTCOMES = frozenset({ExecutionOutcome.REQUEUED, ExecutionOutcome.PAUSED})


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Terminal outcome plus optional pause window (for PAUSED) and per-rule redelivery budget.

    `requeue_limit` carries the matched policy's budget onto REQUEUE/PAUSE outcomes; `None` means the
    endpoint falls back to its own `max_requeue_attempts`. Named fields over PolicyOutcome.
    """

    outcome: ExecutionOutcome
    pause_duration: timedelta | None = None
    requeue_limit: int | None = None


_ResultObserver: TypeAlias = 'Callable[[ExecutionOutcome, Exception | None], Awaitable[None]]'


async def _noop_result_observer(outcome: ExecutionOutcome, exc: Exception | None) -> None:
    """Null result observer: the default so ``execute`` feeds every outcome without branching on absence."""


class EndpointExecutor:
    """Scope-per-attempt execution with retry, dead-letter, and timeout. Endpoints delegate entirely."""

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

    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: _ResultObserver = _noop_result_observer,
    ) -> ExecutionResult:
        if self._is_expired(envelope):
            # Intentional expiry → DISCARD (never DLQ); deletes the row on both live and recovery paths.
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
            try:
                await self._dispatch_in_scope(envelope, handler_type)
            except Exception as exc:
                outcome = self._evaluate(envelope, handler_type, exc, attempt)
                if outcome is None:
                    logger.exception('%s failed: message_id=%s', handler_type.__name__, envelope.message_id)
                    return ExecutionResult(ExecutionOutcome.FAILED_NO_POLICY), exc
                result = await self._handle_failure(outcome, envelope, exc, attempt)
                if result is None:
                    continue
                return result, exc
            else:
                return ExecutionResult(ExecutionOutcome.SUCCESS), None

    def _resolve_timeout(self, handler_type: HandlerType) -> timedelta | None:
        value = handler_type.execution_timeout
        return self._default_execution_timeout if value is MISSING else value  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows

    async def _dispatch_in_scope(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        # Overrun raises HandlerTimeoutError (not the handler's own TimeoutError) so error_policies can match it.
        timeout = self._resolve_timeout(handler_type)
        with anyio.move_on_after(timeout.total_seconds() if timeout is not None else None) as cancel_scope:
            async with self._container() as scope:
                with message_context_scope(envelope):
                    await self._invoker.invoke(scope, envelope.payload, handler_type)
        if cancel_scope.cancelled_caught:
            raise HandlerTimeoutError(envelope.message_id, timeout)

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
        """Apply a failure policy; returns None to retry, or the terminal ExecutionResult.

        Failed DLQ write → DEAD_LETTER_FAILED (inbox row survives for recovery, ERR-2).
        """
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
                    outcome.retry_delay or 0,
                )
                if outcome.retry_delay:
                    await self._sleep(outcome.retry_delay)
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

    async def write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> bool:
        """Persist *envelope* as a dead-letter entry; returns whether the write committed.

        The store is always resolvable (``DiscardingDeadLetterStore`` when unconfigured); the UoW is
        resolve-or-noop so a pure-BUFFERED app with no durability (and thus no UoW) never crashes here.
        """
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            codec = await scope.get(PayloadCodec)
            try:
                uow: IUnitOfWork = await scope.get(IUnitOfWork)
            except NoFactoryError:
                uow = NoOpUnitOfWork()
            entry = DeadLetterEntry.from_failure(
                message_type=envelope.message_type,
                payload=encode_payload(envelope, codec),
                destination=self._endpoint_uri,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                exc=exc,
                attempt=attempt,
                message_id=envelope.message_id,
                metadata=encode_metadata(envelope),
                group_id=envelope.group_id,
            )
            try:
                await store.save(entry)
                await uow.commit()
            except Exception:
                logger.exception('Failed to write dead letter entry for message_id=%s', envelope.message_id)
                return False
            return True


class EndpointExecutorFactory:
    """Single construction seam for EndpointExecutor across the live, listener, and recovery paths.

    Captures the app-scope collaborators once so every URI's executor shares identical deadline
    (default_execution_timeout) and clock (now) semantics; only the URI (and its observers) varies.
    """

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
        self._cache: dict[str, EndpointExecutor] = {}

    def for_uri(self, endpoint_uri: str) -> EndpointExecutor:
        executor = self._cache.get(endpoint_uri)
        if executor is None:
            executor = EndpointExecutor(
                container=self._container,
                evaluator=self._evaluator,
                endpoint_uri=endpoint_uri,
                invoker=self._invoker,
                observers=self._plan.for_endpoint(endpoint_uri),
                default_execution_timeout=self._default_execution_timeout,
                now=self._now,
            )
            self._cache[endpoint_uri] = executor
        return executor
