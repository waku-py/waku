from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.contracts.message import IMessage
    from waku.messaging.errors.policy import ResolvedRetryPolicy

__all__ = [
    'ErrorPolicyRegistry',
]


class DuplicateErrorPolicyError(ImproperlyConfiguredError):
    def __init__(self, policy: ResolvedRetryPolicy) -> None:
        exc_name = policy.exception_type.__qualname__ if policy.exception_type is not None else '*'
        super().__init__(f'Duplicate error policy for {policy.message_type.__qualname__} / {exc_name}')


class ErrorPolicyRegistry:
    __slots__ = ('_specific', '_wildcard')

    def __init__(self, policies: Sequence[ResolvedRetryPolicy]) -> None:
        self._specific: dict[tuple[type[IMessage], type[Exception]], ResolvedRetryPolicy] = {}
        self._wildcard: dict[type[IMessage], ResolvedRetryPolicy] = {}
        for policy in policies:
            if policy.exception_type is None:
                if policy.message_type in self._wildcard:
                    raise DuplicateErrorPolicyError(policy)
                self._wildcard[policy.message_type] = policy
            else:
                key = (policy.message_type, policy.exception_type)
                if key in self._specific:
                    raise DuplicateErrorPolicyError(policy)
                self._specific[key] = policy

    def resolve(self, message_type: type[IMessage], exc: Exception) -> ResolvedRetryPolicy | None:
        for exc_class in type(exc).__mro__:
            policy = self._specific.get((message_type, exc_class))
            if policy is not None:
                return policy
        return self._wildcard.get(message_type)
