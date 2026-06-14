from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.exceptions import ImproperlyConfiguredError

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
        per_handler = self._handler_policies.get(handler_type, ())
        match = _best_match(per_handler, exc)
        if match is not None:
            return match
        return _best_match(self._default_policies, exc)


def _best_match(policies: Sequence[ErrorPolicy], exc: Exception) -> ErrorPolicy | None:
    # Most specific match wins (predicate > type-only > any); first-match on ties.
    best: ErrorPolicy | None = None
    best_score = -1
    for policy in policies:
        if _policy_matches(policy, exc) and (score := _specificity(policy)) > best_score:
            best = policy
            best_score = score
    return best


def _specificity(policy: ErrorPolicy) -> int:
    return (2 if policy.exception_type is not None else 0) + (1 if policy.predicate is not None else 0)


def _policy_matches(policy: ErrorPolicy, exc: Exception) -> bool:
    if policy.exception_type is not None and not isinstance(exc, policy.exception_type):
        return False
    return policy.predicate is None or policy.predicate(exc)


def _reject_duplicates(handler_type: _HandlerType | None, policies: Sequence[ErrorPolicy]) -> None:
    seen: set[type[Exception] | None] = set()
    for policy in policies:
        if policy.predicate is not None:
            # Predicates are identity-distinct — two predicate policies never collide.
            continue
        if policy.exception_type in seen:
            raise DuplicateErrorPolicyError(handler_type, policy)
        seen.add(policy.exception_type)
