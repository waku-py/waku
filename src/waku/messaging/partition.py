from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Any, TypeAlias

from waku.messaging._identifiers import GroupId

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import IMessage

__all__ = [
    'ISequenceAllocator',
    'PartitionKeyExtractor',
    'resolve_and_allocate',
    'resolve_group_id',
]

logger = logging.getLogger(__name__)

# Returns a partition key (group_id) or None for keyless (parallel, no ordering guarantee).
PartitionKeyExtractor: TypeAlias = 'Callable[[IMessage], str | None]'


class ISequenceAllocator(abc.ABC):
    @abc.abstractmethod
    async def allocate(self, group_id: GroupId) -> int:
        """Atomically allocate the next sequence number for ``group_id``.

        MUST run in the same transaction as the row insert so the sequence is co-committed;
        per-group row lock + MVCC ensure FIFO.
        """


def resolve_group_id(
    envelope: MessageEnvelope[Any],
    partition_by: PartitionKeyExtractor | None,
) -> GroupId | None:
    """Resolve partition key without allocating: ``envelope.group_id`` → ``partition_by(payload)`` → ``None``.

    Keyless messages bypass sequencing (unordered). Public so scheduled dispatch can resolve the key
    without allocating — allocation is deferred to promotion so delayed messages sort after queued siblings.
    """
    raw_group_id = envelope.group_id
    if raw_group_id is None and partition_by is not None:
        raw_group_id = partition_by(envelope.payload)
    if raw_group_id is None:
        logger.debug('Keyless message %s bypassing sequencing (order not guaranteed)', envelope.message_type)
        return None
    return GroupId(raw_group_id)


async def resolve_and_allocate(
    envelope: MessageEnvelope[Any],
    partition_by: PartitionKeyExtractor | None,
    scope: AsyncContainer,
) -> tuple[GroupId | None, int | None]:
    """Resolve partition key and allocate a sequence number if keyed; keyless returns ``(None, None)``.

    Single source of truth for outbox and durable-inbox allocation.
    """
    group_id = resolve_group_id(envelope, partition_by)
    if group_id is None:
        return None, None
    allocator = await scope.get(ISequenceAllocator)
    return group_id, await allocator.allocate(group_id)
