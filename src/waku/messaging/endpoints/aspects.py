from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku._internal.sentinel import MISSING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.sending.policy import SendingFailurePolicy

__all__ = [
    'ListenAspect',
    'SendAspect',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenAspect:
    max_requeue_attempts: int | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    backpressure: BufferingLimits | None = None
    # Reserved, NOT built now: scope (ListenerScope, gap #13); durability (non-durable consume). Spec §11.


@dataclass(frozen=True, slots=True, kw_only=True)
class SendAspect:
    sending_failure_policies: Sequence[SendingFailurePolicy] = ()
