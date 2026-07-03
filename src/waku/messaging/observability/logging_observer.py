import logging
from datetime import timedelta
from typing import Any

from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.observability.audit import AuditedMemberResolver
from waku.messaging.observability.observer import IMessageObserver

__all__ = ['LoggingMessageObserver']

_LOGGER_ROOT = 'waku.message'
_FAILURE_OUTCOMES = frozenset({
    ExecutionOutcome.DEAD_LETTERED,
    ExecutionOutcome.DEAD_LETTER_FAILED,
    ExecutionOutcome.FAILED_NO_POLICY,
})
_WARNING_OUTCOMES = frozenset({ExecutionOutcome.REQUEUED, ExecutionOutcome.PAUSED})


def _executed_level(outcome: ExecutionOutcome) -> int:
    if outcome in _FAILURE_OUTCOMES:
        return logging.ERROR
    if outcome in _WARNING_OUTCOMES:
        return logging.WARNING
    return logging.INFO


class LoggingMessageObserver(IMessageObserver):
    __slots__ = ('_loggers', '_resolver')

    def __init__(self, resolver: AuditedMemberResolver) -> None:
        self._resolver = resolver
        self._loggers: dict[str, logging.Logger] = {}

    def _logger(self, message_type: str) -> logging.Logger:
        cached = self._loggers.get(message_type)
        if cached is None:
            cached = logging.getLogger(f'{_LOGGER_ROOT}.{message_type}')
            self._loggers[message_type] = cached
        return cached

    def _base_fields(self, envelope: MessageEnvelope[Any]) -> dict[str, Any]:
        return {
            'message_id': str(envelope.message_id),
            'correlation_id': str(envelope.correlation_id),
            'causation_id': str(envelope.causation_id),
            'group_id': envelope.group_id,
            'message_type': envelope.message_type,
            'audit': self._resolver.extract(envelope.payload),
        }

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        log = self._logger(envelope.message_type)
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug('sent', extra={**self._base_fields(envelope), 'destination': destination})

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        log = self._logger(envelope.message_type)
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug(
            'executing',
            extra={**self._base_fields(envelope), 'destination': destination, 'handler': handler_type.__qualname__},
        )

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
        log = self._logger(envelope.message_type)
        level = _executed_level(outcome)
        if not log.isEnabledFor(level):
            return
        extra = {
            **self._base_fields(envelope),
            'destination': destination,
            'handler': handler_type.__qualname__,
            'outcome': outcome.value,
            'duration_ms': duration.total_seconds() * 1000,
        }
        if exc is not None:
            extra['error_type'] = type(exc).__name__
            extra['error_message'] = str(exc)
        log.log(level, 'executed', extra=extra)
