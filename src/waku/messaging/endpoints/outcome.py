import enum

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
