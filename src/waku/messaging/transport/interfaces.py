from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope


class ITransport(abc.ABC):
    @abc.abstractmethod
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None: ...
