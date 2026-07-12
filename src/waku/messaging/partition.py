from __future__ import annotations

import abc
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.messages import IMessage
    from waku.messaging._internal.identifiers import GroupId

__all__ = [
    'ISequenceAllocator',
    'PartitionKeyExtractor',
]

# Returns a partition key (group_id) or None for keyless (parallel, no ordering guarantee).
PartitionKeyExtractor: TypeAlias = 'Callable[[IMessage], str | None]'


class ISequenceAllocator(abc.ABC):
    @abc.abstractmethod
    async def allocate(self, group_id: GroupId) -> int:
        """Atomically allocate the next sequence number for ``group_id``.

        MUST run in the same transaction as the row insert so the sequence is co-committed;
        per-group row lock + MVCC ensure FIFO.
        """
