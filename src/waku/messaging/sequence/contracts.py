import abc
from typing import NewType

__all__ = [
    'GroupId',
    'ISequenceAllocator',
]

# Follows the persisted-identity NewType guard convention (see waku.messaging.inbox.identifiers).
GroupId = NewType('GroupId', str)


class ISequenceAllocator(abc.ABC):
    @abc.abstractmethod
    async def allocate(self, group_id: GroupId) -> int:
        """Atomically allocate the next sequence number for ``group_id``.

        MUST run in the same transaction as the row insert so the sequence is co-committed;
        per-group row lock + MVCC ensure FIFO.
        """
