from __future__ import annotations

from typing import TYPE_CHECKING

from waku.messaging.inbox.identifiers import HandlerDestination

if TYPE_CHECKING:
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.handler_map import HandlerMap

__all__ = ['handler_destination']


def handler_destination(handler_type: HandlerType) -> HandlerDestination:
    """Per-handler inbox dedup discriminator: the handler FQN ``{module}.{qualname}``.

    Single source for the composite-key ``destination`` so ``DurableLocalQueueEndpoint`` and
    the inbox drainer cannot drift (a mismatch would silently break dedup).
    """
    return HandlerDestination(f'{handler_type.__module__}.{handler_type.__qualname__}')


def handler_map_by_destination(handler_map: HandlerMap) -> dict[HandlerDestination, HandlerType]:
    """Reverse ``destination -> handler`` map, keyed identically to ``handler_destination``.

    Single builder for the inbox drainer and DLQ replay lookups so the key type cannot drift.
    """
    return {handler_destination(handler_type): handler_type for handler_type in handler_map.handler_types()}
