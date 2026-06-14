from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waku.messaging.contracts.handler import HandlerType

__all__ = ['handler_destination']


def handler_destination(handler_type: HandlerType) -> str:
    """Per-handler inbox dedup discriminator: the handler FQN ``{module}.{qualname}``.

    Single source for the composite-key ``destination`` so ``DurableReceiver`` and
    ``DurableLocalQueueEndpoint`` cannot drift (a mismatch would silently break dedup).
    """
    return f'{handler_type.__module__}.{handler_type.__qualname__}'
