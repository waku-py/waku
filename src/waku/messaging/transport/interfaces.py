from __future__ import annotations

import abc
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from datetime import datetime

    from waku.messaging.transport.inbound import ConsumeCallback

__all__ = [
    'EnvelopeMetadata',
    'IEnvelopeMapper',
    'IListener',
    'ISender',
    'ITransport',
    'Subscription',
    'TransportFactory',
]

TIncoming = TypeVar('TIncoming')
TOutgoing = TypeVar('TOutgoing')


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvelopeMetadata:
    """All non-payload envelope fields — correlation IDs, routing key, version, scheduling, and user headers.

    Passed to transports when publishing; persisted and projected directly by infrastructure adapters.
    ``group_id`` is the partition-routing key. For Kafka it maps to the message key; for other brokers
    it rides as a wire header.
    Per-transport wire-header projection is owned by the ``IEnvelopeMapper`` for each broker.
    """

    message_id: str
    correlation_id: str
    causation_id: str
    message_type: str
    group_id: str | None = None
    message_version: int = 1
    timestamp: datetime | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    scheduled_time: datetime | None = None
    expires_at: datetime | None = None


class IEnvelopeMapper(abc.ABC, Generic[TIncoming, TOutgoing]):
    """Generic broker envelope mapper — owns the wire format for both directions.

    Broker-specific subtypes specialise the type parameters:
    ``IKafkaEnvelopeMapper`` is ``IEnvelopeMapper[KafkaMessage, KafkaOutgoing]``.
    Pass an ``IEnvelopeMapper[Any, Any]`` wherever the concrete broker type is not known at the call site
    (e.g. per-endpoint config, ``IListener.subscribe``).
    """

    @abc.abstractmethod
    def map_outgoing(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> TOutgoing: ...

    @abc.abstractmethod
    async def map_incoming(self, msg: TIncoming) -> tuple[dict[str, Any], EnvelopeMetadata]: ...


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
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None: ...


class IListener(abc.ABC):
    @abc.abstractmethod
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        """Register a consumer and return its pause handle — no broker I/O, purely a registration step.

        When *mapper* is provided it overrides the transport's default per-scheme mapper for this subscription
        only, enabling per-endpoint wire-format customisation (e.g. legacy interop queues).
        """

    @abc.abstractmethod
    async def start(self) -> None:
        """Open broker connection and activate registered consumers.  Idempotent."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Drain in-flight messages and close the broker connection."""


class ITransport(ISender, IListener, abc.ABC): ...


TransportFactory = Callable[[], ITransport]
