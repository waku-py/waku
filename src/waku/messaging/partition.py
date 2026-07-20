from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.messages import IMessage
__all__ = [
    'PartitionKeyExtractor',
]

# Returns a partition key (group_id) or None for keyless (parallel, no ordering guarantee).
PartitionKeyExtractor: TypeAlias = 'Callable[[IMessage], str | None]'
