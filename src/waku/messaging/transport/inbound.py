from __future__ import annotations

import abc
import enum
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    'ConsumeCallback',
    'ConsumeDisposition',
    'IInboundTransport',
]

ConsumeCallback: TypeAlias = 'Callable[[dict[str, Any]], Awaitable[ConsumeDisposition]]'


class ConsumeDisposition(enum.Enum):
    ACK = 'ACK'
    NACK_REQUEUE = 'NACK_REQUEUE'
    REJECT = 'REJECT'


class IInboundTransport(abc.ABC):
    @abc.abstractmethod
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> None:
        """Register a consumer for ``queue``.  No broker I/O — purely a registration step."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Open broker connection and activate all registered consumers.  Idempotent."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Drain in-flight messages and close the broker connection."""
