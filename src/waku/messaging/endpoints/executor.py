from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import anyio

from waku._internal.clock import utc_now
from waku._internal.transaction import TransactionCleanupError
from waku.messaging._internal.transaction import CompletedExecutionError
from waku.messaging.endpoints._internal.execution import (
    EndpointExecution,
    ExecutionResult,
    ResultObserver,
    noop_result_observer,
)

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


class EndpointExecutor:
    """Public endpoint execution boundary that exposes the original rollback failure."""

    __slots__ = ('_execution',)

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

    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> ExecutionResult:
        try:
            return await self._execution.execute(envelope, handler_type, on_result=on_result)
        except TransactionCleanupError as exc:
            raise exc.rollback_error from exc.primary_error
        except CompletedExecutionError as exc:
            raise exc.error from exc

    async def write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> bool:
        try:
            return await self._execution.write_dead_letter(envelope, exc, attempt)
        except TransactionCleanupError as cleanup_error:
            raise cleanup_error.rollback_error from cleanup_error.primary_error


class EndpointExecutorFactory:
    """Build and memoize public EndpointExecutor boundaries by endpoint URI."""

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
