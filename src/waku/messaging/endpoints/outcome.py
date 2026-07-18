import enum
from typing import Final

__all__ = [
    'ExecutionOutcome',
]


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


# Single owner of the failure / deferred-terminal taxonomy. The circuit breaker counts FAILURE_OUTCOMES
# toward tripping and the logging observer escalates them to ERROR; both consult these sets rather than
# restating them, so no shotgun edit can desync the tripping set from the ERROR-escalation set.
FAILURE_OUTCOMES: Final[frozenset[ExecutionOutcome]] = frozenset({
    ExecutionOutcome.DEAD_LETTERED,
    ExecutionOutcome.DEAD_LETTER_FAILED,
    ExecutionOutcome.FAILED_NO_POLICY,
})
DEFERRED_TERMINAL_OUTCOMES: Final[frozenset[ExecutionOutcome]] = frozenset({
    ExecutionOutcome.REQUEUED,
    ExecutionOutcome.PAUSED,
})
