from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope


@runtime_checkable
class ITransport(Protocol):
    async def send(self, envelope: MessageEnvelope[Any]) -> None: ...
    async def publish(self, envelope: MessageEnvelope[Any]) -> None: ...
