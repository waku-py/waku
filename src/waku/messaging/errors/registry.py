from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.escalation import resolve_with_default

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.messaging.errors.policy import ErrorPolicy
    from waku.messaging.handler import MessageHandler

__all__ = [
    'DuplicateErrorPolicyError',
    'ErrorPolicyRegistry',
]


_HandlerType = type['MessageHandler[Any, Any]']


class DuplicateErrorPolicyError(ImproperlyConfiguredError):
    def __init__(self, handler_type: _HandlerType | None, policy: ErrorPolicy) -> None:
        exc_name = policy.exception_type.__qualname__ if policy.exception_type is not None else '*'
        scope = handler_type.__qualname__ if handler_type is not None else '<default>'
        super().__init__(f'Duplicate error policy for {scope} / {exc_name}')


class ErrorPolicyRegistry:
    """Per-handler and default error policies; resolves the best match for a handler and exception."""

    __slots__ = ('_default_policies', '_handler_policies')

    def __init__(
        self,
        *,
        handler_policies: Mapping[_HandlerType, Sequence[ErrorPolicy]],
        default_policies: Sequence[ErrorPolicy],
        strict: bool = False,
    ) -> None:
        self._handler_policies: dict[_HandlerType, tuple[ErrorPolicy, ...]] = {}
        for handler_type, policies in handler_policies.items():
            if strict:
                _reject_duplicates(handler_type, policies)
            self._handler_policies[handler_type] = tuple(policies)
        if strict:
            _reject_duplicates(None, default_policies)
        self._default_policies: tuple[ErrorPolicy, ...] = tuple(default_policies)

    def resolve(self, handler_type: _HandlerType, exc: Exception) -> ErrorPolicy | None:
        return resolve_with_default(self._handler_policies.get(handler_type, ()), self._default_policies, exc)


def _reject_duplicates(handler_type: _HandlerType | None, policies: Sequence[ErrorPolicy]) -> None:
    seen: set[type[Exception] | None] = set()
    for policy in policies:
        if policy.predicate is not None:
            # Predicates are identity-distinct — two predicate policies never collide.
            continue
        if policy.exception_type in seen:
            raise DuplicateErrorPolicyError(handler_type, policy)
        seen.add(policy.exception_type)
