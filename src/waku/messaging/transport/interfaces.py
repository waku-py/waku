from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.messaging.transport.inbound import ConsumeCallback

__all__ = [
    'IListener',
    'ISender',
    'ITransport',
    'Subscription',
    'TransportFactory',
    'WireMetadata',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class WireMetadata:
    """Message-derived attributes the transport stamps onto the wire when publishing.

    The correlation fields become broker headers (``as_headers``); ``group_id`` becomes the
    partition-routing key (e.g. the Kafka message key). Keeps both concerns off the serializer.
    """

    message_id: str
    correlation_id: str
    causation_id: str
    message_type: str
    group_id: str | None = None

    def as_headers(self) -> dict[str, str]:
        return {
            'message_id': self.message_id,
            'correlation_id': self.correlation_id,
            'causation_id': self.causation_id,
            'message_type': self.message_type,
        }


class Subscription(abc.ABC):
    """Per-subscriber pause handle returned by ``IListener.subscribe`` — stop/resume one consumer's delivery."""

    @abc.abstractmethod
    async def pause(self) -> None:
        """Stop broker delivery for this subscriber (other subscribers unaffected)."""

    @abc.abstractmethod
    async def resume(self) -> None:
        """Resume broker delivery for this subscriber."""


class ISender(abc.ABC):
    @abc.abstractmethod
    async def send(self, body: dict[str, Any], *, destination: str, metadata: WireMetadata) -> None: ...


class IListener(abc.ABC):
    @abc.abstractmethod
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> Subscription:
        """Register a consumer and return its pause handle — no broker I/O, purely a registration step."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Open broker connection and activate registered consumers.  Idempotent."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Drain in-flight messages and close the broker connection."""


class ITransport(ISender, IListener, abc.ABC): ...


TransportFactory = Callable[[], ITransport]
