from __future__ import annotations

from typing import TYPE_CHECKING

from waku.messaging.inbox.identifiers import HandlerDestination

if TYPE_CHECKING:
    from waku.messaging.contracts.handler import HandlerType

__all__ = ['handler_destination']


def handler_destination(handler_type: HandlerType) -> HandlerDestination:
    """Per-handler inbox dedup discriminator: the handler FQN ``{module}.{qualname}``.

    Single source for the composite-key ``destination`` so ``DurableLocalQueueEndpoint`` and
    the inbox drainer cannot drift (a mismatch would silently break dedup).
    """
    return HandlerDestination(f'{handler_type.__module__}.{handler_type.__qualname__}')
