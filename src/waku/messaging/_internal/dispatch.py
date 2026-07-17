from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messages import IMessage
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.base import Endpoint

__all__ = ['IEndpointDispatch']


class IEndpointDispatch(abc.ABC):
    """Internal destination-dispatch port: deliver one message to an explicit endpoint subset.

    Narrower than ``ISender``/``IPublisher`` — the caller has ALREADY resolved (and possibly
    partitioned) the destinations, so no route resolution happens here. Action-agnostic: at the
    endpoint level ``dispatch`` is identical for send and publish. Implemented by ``MessageBus``
    (and DI-aliased to the same scoped instance) so the envelope inherits the live
    ``MessageContext`` — correlation/causation/group/headers — exactly as bus-routed dispatch does.
    """

    @abc.abstractmethod
    async def dispatch_to(self, message: IMessage, endpoints: Sequence[Endpoint]) -> MessageEnvelope[Any] | None:
        """Dispatch ``message`` to exactly ``endpoints``, wrapped in one context-propagated envelope.

        Returns the created envelope, or ``None`` when ``endpoints`` is empty. A caller staging a durable
        leg records post-commit ``sent`` evidence against this exact envelope (the staged outbox row's).
        """
