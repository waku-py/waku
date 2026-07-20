from __future__ import annotations

from dataclasses import dataclass

from waku.messaging._internal.escalation import EscalationChain

__all__ = [
    'SendingFailurePolicy',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SendingFailurePolicy(EscalationChain['SendingFailurePolicy']):
    """An ordered outbound-send escalation chain, resolved per destination URI.

    A disjoint domain from handler `ErrorPolicy`: applied by the poll-based outbox relay
    (`retry` reschedules to the next poll, `retry_with_backoff` sets `next_retry_at = now + backoff`),
    and REQUIRING an explicit terminal — a retry-only chain would silently drop a persisted message on
    exhaustion. That explicit-terminal invariant is enforced at `SendingFailurePolicyRegistry` build
    time, keeping the fluent builder's intermediate retry-only states constructible.
    """
