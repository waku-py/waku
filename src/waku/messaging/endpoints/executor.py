from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Any, assert_never

import anyio

from waku.messaging.context import message_context_scope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.executor import FailureContext
from waku.messaging.errors.policy import RetryAction
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.executor import ErrorPolicyEvaluator, PolicyOutcome
    from waku.messaging.identity import MessageTypeRegistry
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
    DISCARDED = 'DISCARDED'
    FAILED_NO_POLICY = 'FAILED_NO_POLICY'


class EndpointExecutor:
    """Executes message handlers with scope-per-attempt lifecycle, retry, and dead letter support.

    Sits between endpoint workers and the pipeline.
    Endpoints delegate to this class; they do not manage scopes, retries, or error handling directly.
    """

    __slots__ = ('_container', '_endpoint_uri', '_evaluator', '_invoker', '_registry')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        evaluator: ErrorPolicyEvaluator,
        endpoint_uri: str,
        invoker: HandlerPipelineInvoker,
        registry: MessageTypeRegistry,
    ) -> None:
        self._container = container
        self._evaluator = evaluator
        self._endpoint_uri = endpoint_uri
        self._invoker = invoker
        self._registry = registry

    async def execute(self, envelope: MessageEnvelope[Any], handler_type: HandlerType) -> ExecutionOutcome:
        attempt = 0
        while True:
            attempt += 1
            try:
                await self._dispatch_in_scope(envelope, handler_type)
            except Exception as exc:
                outcome = self._evaluate(envelope, handler_type, exc, attempt)
                if outcome is None:
                    logger.exception('%s failed: message_id=%s', handler_type.__name__, envelope.message_id)
                    return ExecutionOutcome.FAILED_NO_POLICY
                if await self._apply_outcome(outcome, envelope, exc, attempt):
                    continue
                if outcome.action is RetryAction.DEAD_LETTER:
                    return ExecutionOutcome.DEAD_LETTERED
                return ExecutionOutcome.DISCARDED
            else:
                return ExecutionOutcome.SUCCESS

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

    async def _apply_outcome(
        self,
        outcome: PolicyOutcome,
        envelope: MessageEnvelope[Any],
        exc: Exception,
        attempt: int,
    ) -> bool:
        """Apply policy outcome. Returns True if execution should retry."""
        match outcome.action:
            case RetryAction.DEAD_LETTER:
                logger.warning('Moving message_id=%s to dead letter after %d attempt(s)', envelope.message_id, attempt)
                await self._write_dead_letter(envelope, exc, attempt)
                return False
            case RetryAction.DISCARD:
                logger.info('Discarded message_id=%s after %d attempt(s)', envelope.message_id, attempt)
                return False
            case RetryAction.RETRY | RetryAction.RETRY_WITH_BACKOFF:
                logger.info(
                    'Retrying message_id=%s (attempt %d, delay=%.2fs)',
                    envelope.message_id,
                    attempt,
                    outcome.retry_delay or 0,
                )
                if outcome.retry_delay:
                    await anyio.sleep(outcome.retry_delay)
                return True
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)

    async def _write_dead_letter(self, envelope: MessageEnvelope[Any], exc: Exception, attempt: int) -> None:
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            serializer = await scope.get(IEnvelopeSerializer)
            uow = await scope.get(IUnitOfWork)
            entry = DeadLetterEntry.from_failure(
                message_type=self._registry.resolve_name(type(envelope.payload)),
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
