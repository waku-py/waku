from __future__ import annotations

from typing import TYPE_CHECKING

from waku.messaging._internal.escalation import best_match, validate_ends_with_terminal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.messaging.sending.policy import SendingFailurePolicy

__all__ = [
    'SendingFailurePolicyRegistry',
]


class SendingFailurePolicyRegistry:
    """Per-destination sending failure policy registry.

    Keyed by destination URI (the disjoint mirror of the handler-keyed `ErrorPolicyRegistry`).
    `resolve(destination, exc)` does per-destination `best_match` → global-default `best_match`.

    Enforces the durable-domain invariant at construction: every registered policy MUST end in a
    terminal stage (`validate_ends_with_terminal`) — a registered retry-only chain would silently
    drop a persisted outbox message on exhaustion. Fails fast at registry build (app startup).
    """

    __slots__ = ('_default_policies', '_destination_policies')

    def __init__(
        self,
        *,
        destination_policies: Mapping[str, Sequence[SendingFailurePolicy]],
        default_policies: Sequence[SendingFailurePolicy],
    ) -> None:
        self._destination_policies: dict[str, tuple[SendingFailurePolicy, ...]] = {
            destination: tuple(policies) for destination, policies in destination_policies.items()
        }
        self._default_policies: tuple[SendingFailurePolicy, ...] = tuple(default_policies)
        for policies in (*self._destination_policies.values(), self._default_policies):
            for policy in policies:
                validate_ends_with_terminal(policy.stages)

    def resolve(self, destination: str, exc: Exception) -> SendingFailurePolicy | None:
        per_destination = self._destination_policies.get(destination, ())
        match = best_match(per_destination, exc)
        if match is not None:
            return match
        return best_match(self._default_policies, exc)
