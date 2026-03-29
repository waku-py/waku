from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage

__all__ = [
    'ResolvedRetryPolicy',
    'RetryAction',
    'RetryPolicy',
]


class RetryAction(enum.Enum):
    RETRY = 'RETRY'
    RETRY_WITH_BACKOFF = 'RETRY_WITH_BACKOFF'
    DISCARD = 'DISCARD'
    DEAD_LETTER = 'DEAD_LETTER'


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedRetryPolicy:
    message_type: type[IMessage]
    exception_type: type[Exception] | None
    action: RetryAction
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    fallback_action: RetryAction | None = None


class _RetryActionBuilder:
    __slots__ = ('_exception_type', '_message_type')

    def __init__(self, message_type: type[IMessage], exception_type: type[Exception] | None) -> None:
        self._message_type = message_type
        self._exception_type = exception_type

    def retry(
        self,
        max_attempts: int = 3,
        fallback: RetryAction | None = None,
    ) -> ResolvedRetryPolicy:
        return ResolvedRetryPolicy(
            message_type=self._message_type,
            exception_type=self._exception_type,
            action=RetryAction.RETRY,
            max_attempts=max_attempts,
            fallback_action=fallback,
        )

    def retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        fallback: RetryAction | None = None,
    ) -> ResolvedRetryPolicy:
        return ResolvedRetryPolicy(
            message_type=self._message_type,
            exception_type=self._exception_type,
            action=RetryAction.RETRY_WITH_BACKOFF,
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            fallback_action=fallback,
        )

    def discard(self) -> ResolvedRetryPolicy:
        return ResolvedRetryPolicy(
            message_type=self._message_type,
            exception_type=self._exception_type,
            action=RetryAction.DISCARD,
        )

    def move_to_dead_letter(self) -> ResolvedRetryPolicy:
        return ResolvedRetryPolicy(
            message_type=self._message_type,
            exception_type=self._exception_type,
            action=RetryAction.DEAD_LETTER,
        )


class _RetryPolicyBuilder:
    __slots__ = ('_message_type',)

    def __init__(self, message_type: type[IMessage]) -> None:
        self._message_type = message_type

    def on_exception(self, exception_type: type[Exception]) -> _RetryActionBuilder:
        return _RetryActionBuilder(self._message_type, exception_type)

    def on_any_exception(self) -> _RetryActionBuilder:
        return _RetryActionBuilder(self._message_type, None)


class RetryPolicy:
    @staticmethod
    def for_message(message_type: type[IMessage]) -> _RetryPolicyBuilder:
        return _RetryPolicyBuilder(message_type)
