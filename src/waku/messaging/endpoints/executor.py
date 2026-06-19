from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Any, TypeAlias, assert_never

import anyio

from waku.messaging.context import message_context_scope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.executor import FailureContext
from waku.messaging.errors.policy import RetryAction
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.executor import ErrorPolicyEvaluator, PolicyOutcome
    from waku.messaging.pipeline.invoker import HandlerPipelineInvoker

__all__ = [
    'EndpointExecutor',
    'ExecutionOutcome',
]

logger = logging.getLogger(__name__)


@enum.unique
class ExecutionOutcome(enum.Enum):
    SUCCESS = 'SUCCESS'
    DEAD_LETTERED = 'DEAD_LETTERED'
    DEAD_LETTER_FAILED = 'DEAD_LETTER_FAILED'  # DLQ write did not persist; keep a durable row for recovery
    DISCARDED = 'DISCARDED'
    FAILED_NO_POLICY = 'FAILED_NO_POLICY'


_ResultObserver: TypeAlias = 'Callable[[ExecutionOutcome, Exception | None], Awaitable[None]]'


class EndpointExecutor:
    """Executes message handlers with scope-per-attempt lifecycle, retry, and dead letter support.

    Sits between endpoint workers and the pipeline.
    Endpoints delegate to this class; they do not manage scopes, retries, or error handling directly.
    """

    __slots__ = ('_container', '_endpoint_uri', '_evaluator', '_invoker', '_sleep')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        evaluator: ErrorPolicyEvaluator,
        endpoint_uri: str,
        invoker: HandlerPipelineInvoker,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._container = container
        self._evaluator = evaluator
        self._endpoint_uri = endpoint_uri
        self._invoker = invoker
        self._sleep = sleep

    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        *,
        on_result: _ResultObserver | None = None,
    ) -> ExecutionOutcome:
        outcome, exc = await self._run_attempts(envelope, handler_type)
        # Fire once per handler-execution with the TERMINAL outcome — never per retry-attempt. The
        # circuit breaker samples message throughput, so a message retried N times is one data-point.
        if on_result is not None:
            await on_result(outcome, exc)
        return outcome

    async def _run_attempts(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> tuple[ExecutionOutcome, Exception | None]:
        attempt = 0
        while True:
            attempt += 1
            try:
                await self._dispatch_in_scope(envelope, handler_type)
            except Exception as exc:
                outcome = self._evaluate(envelope, handler_type, exc, attempt)
                if outcome is None:
                    logger.exception('%s failed: message_id=%s', handler_type.__name__, envelope.message_id)
                    return ExecutionOutcome.FAILED_NO_POLICY, exc
                terminal = await self._handle_failure(outcome, envelope, exc, attempt)
                if terminal is None:
                    continue
                return terminal, exc
            else:
                return ExecutionOutcome.SUCCESS, None

    async def _dispatch_in_scope(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
        async with self._container() as scope:
            with message_context_scope(envelope):
                await self._invoker.invoke(scope, envelope.payload, handler_type)

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
    ) -> ExecutionOutcome | None:
        """Apply a failure policy. Returns the terminal ExecutionOutcome, or None to retry.

        DEAD_LETTER routes through _write_dead_letter: a successful write yields DEAD_LETTERED; a write
        that does not persist yields DEAD_LETTER_FAILED so a durable inbox row survives for recovery (ERR-2).
        """
        match outcome.action:
            case RetryAction.DEAD_LETTER:
                logger.warning('Moving message_id=%s to dead letter after %d attempt(s)', envelope.message_id, attempt)
                persisted = await self._write_dead_letter(envelope, exc, attempt)
                return ExecutionOutcome.DEAD_LETTERED if persisted else ExecutionOutcome.DEAD_LETTER_FAILED
            case RetryAction.DISCARD:
                logger.info('Discarded message_id=%s after %d attempt(s)', envelope.message_id, attempt)
                return ExecutionOutcome.DISCARDED
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
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    async def _write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> bool:
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            serializer = await scope.get(IEnvelopeSerializer)
            uow = await scope.get(IUnitOfWork)
            entry = DeadLetterEntry.from_failure(
                message_type=envelope.message_type,
                payload=serializer.serialize(envelope),
                destination=self._endpoint_uri,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                exc=exc,
                attempt=attempt,
            )
            try:
                await store.save(entry)
                await uow.commit()
            except Exception:
                logger.exception('Failed to write dead letter entry for message_id=%s', envelope.message_id)
                return False
            return True
