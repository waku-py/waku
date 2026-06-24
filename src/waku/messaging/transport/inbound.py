from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    'ConsumeCallback',
    'ConsumeDisposition',
]

ConsumeCallback: TypeAlias = 'Callable[[dict[str, Any]], Awaitable[ConsumeDisposition]]'


class ConsumeDisposition(enum.Enum):
    ACK = 'ACK'
    NACK_REQUEUE = 'NACK_REQUEUE'
    REJECT = 'REJECT'
