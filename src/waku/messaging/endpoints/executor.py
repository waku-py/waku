from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Never, assert_never

import anyio

from waku._internal.clock import utc_now
from waku._internal.transaction import Aborted, Commit, Committed, RolledBack, TransactionDecision, execute_in_uow_scope
from waku.messaging.durability import IDurabilityStore
from waku.messaging.endpoints._internal.execution import (
    EndpointExecution,
    ExecutionResult,
    ResultObserver,
    TerminalIntent,
    TerminalIntentKind,
    noop_result_observer,
)
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.sequence import GroupId
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.executor import ErrorPolicyEvaluator
    from waku.messaging.observability.observer import MessageObservers, ObserverPlan
    from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker

__all__ = [
    'EndpointExecutor',
    'EndpointExecutorFactory',
    'ExecutionResult',
]

logger = logging.getLogger(__name__)


def _result_from_intent(intent: TerminalIntent) -> ExecutionResult:
    match intent.kind:
        case TerminalIntentKind.SUCCESS:
            return ExecutionResult(ExecutionOutcome.SUCCESS)
        case TerminalIntentKind.FAILED_NO_POLICY:
            return ExecutionResult(ExecutionOutcome.FAILED_NO_POLICY)
        case TerminalIntentKind.DISCARD:
            return ExecutionResult(ExecutionOutcome.DISCARDED)
        case TerminalIntentKind.REQUEUE:
            return ExecutionResult(ExecutionOutcome.REQUEUED, requeue_limit=intent.requeue_limit)
        case TerminalIntentKind.PAUSE:
            return ExecutionResult(
                ExecutionOutcome.PAUSED,
                pause_duration=intent.pause_duration,
                requeue_limit=intent.requeue_limit,
            )
        case TerminalIntentKind.DEAD_LETTER:
            msg = 'dead-letter intent requires its delivery owner to persist or discard it'
            raise RuntimeError(msg)
        case _ as unreachable:  # pragma: no cover
            assert_never(unreachable)


async def materialize_standalone_dead_letter(
    container: AsyncContainer,
    *,
    envelope: MessageEnvelope[Any],
    endpoint_uri: str,
    intent: TerminalIntent,
) -> ExecutionResult:
    """Persist a buffered/inline DLQ intent in the standalone durability capability's transaction.

    Raises:
        ValueError: if the intent is not a failed dead-letter intent.
    """
    error = intent.error
    if intent.kind is not TerminalIntentKind.DEAD_LETTER or error is None:
        msg = 'only a failed dead-letter intent may use standalone dead-letter materialization'
        raise ValueError(msg)

    async def save(scope: AsyncContainer) -> TransactionDecision[None, Never]:
        durability = await scope.get(IDurabilityStore)
        codec = await scope.get(PayloadCodec)
        entry = DeadLetterEntry.from_failure(
            message_type=envelope.message_type,
            payload=encode_payload(envelope, codec),
            destination=endpoint_uri,
            destination_kind=DeadLetterDestinationKind.ENDPOINT,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            exc=error,
            attempt=intent.attempt,
            message_id=envelope.message_id,
            metadata=encode_metadata(envelope),
            group_id=GroupId(envelope.group_id) if envelope.group_id is not None else None,
        )
        await durability.dead_letters.save(entry)
        return Commit(value=None)

    result = await execute_in_uow_scope(container, save)
    if isinstance(result, Committed):
        return ExecutionResult(ExecutionOutcome.DEAD_LETTERED)
    if isinstance(result, Aborted):
        logger.error('Failed to write dead letter entry for message_id=%s', envelope.message_id, exc_info=result.error)
        return ExecutionResult(ExecutionOutcome.DEAD_LETTER_FAILED)
    if isinstance(result, RolledBack):
        assert_never(result.value)
    assert_never(result)


async def materialize_or_discard_dead_letter(
    container: AsyncContainer,
    *,
    envelope: MessageEnvelope[Any],
    endpoint_uri: str,
    intent: TerminalIntent,
    dead_letter_capable: bool,
    logger: logging.Logger,
) -> ExecutionResult:
    """Persist a dead-letter intent when the endpoint is capable, else warn and discard it.

    The caller supplies its own module ``logger`` so the discard warning keeps its owning logger name.
    """
    if not dead_letter_capable:
        logger.warning(
            'Discarding dead-letter intent without configured durability: message_id=%s was not persisted',
            envelope.message_id,
        )
        return ExecutionResult(ExecutionOutcome.DISCARDED)
    return await materialize_standalone_dead_letter(
        container,
        envelope=envelope,
        endpoint_uri=endpoint_uri,
        intent=intent,
    )


class EndpointExecutor:
    """Public inline endpoint owner that materializes terminal intent after any required persistence."""

    __slots__ = ('_container', '_dead_letter_capable', '_endpoint_uri', '_execution')

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
        dead_letter_capable: bool = False,
    ) -> None:
        self._container = container
        self._execution = EndpointExecution(
            container=container,
            evaluator=evaluator,
            endpoint_uri=endpoint_uri,
            invoker=invoker,
            observers=observers,
            default_execution_timeout=default_execution_timeout,
            sleep=sleep,
            now=now,
            monotonic=monotonic,
        )
        self._endpoint_uri = endpoint_uri
        self._dead_letter_capable = dead_letter_capable

    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> ExecutionResult:
        intent = await self._execution.execute(envelope, handler_type)
        result = await self._materialize(envelope, intent)
        await self._execution.emit_terminal(envelope, handler_type, intent, result, on_result=on_result)
        return result

    async def _materialize(self, envelope: MessageEnvelope[Any], intent: TerminalIntent) -> ExecutionResult:
        if intent.kind is not TerminalIntentKind.DEAD_LETTER:
            return _result_from_intent(intent)
        return await materialize_or_discard_dead_letter(
            self._container,
            envelope=envelope,
            endpoint_uri=self._endpoint_uri,
            intent=intent,
            dead_letter_capable=self._dead_letter_capable,
            logger=logger,
        )


class EndpointExecutorFactory:
    """Build and memoize public EndpointExecutor boundaries by endpoint URI."""

    __slots__ = (
        '_cache',
        '_container',
        '_dead_letter_capable',
        '_default_execution_timeout',
        '_evaluator',
        '_invoker',
        '_now',
        '_plan',
    )

    def __init__(
        self,
        *,
        container: AsyncContainer,
        evaluator: ErrorPolicyEvaluator,
        invoker: HandlerPipelineInvoker,
        plan: ObserverPlan,
        default_execution_timeout: timedelta | None,
        now: Now,
        dead_letter_capable: bool,
    ) -> None:
        self._container = container
        self._evaluator = evaluator
        self._invoker = invoker
        self._plan = plan
        self._default_execution_timeout = default_execution_timeout
        self._now = now
        self._dead_letter_capable = dead_letter_capable
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
                dead_letter_capable=self._dead_letter_capable,
            )
            self._cache[endpoint_uri] = executor
        return executor
