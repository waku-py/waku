from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from waku.messaging._internal.identifiers import GroupId
from waku.messaging.partition import ISequenceAllocator

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.partition import PartitionKeyExtractor

__all__ = [
    'resolve_and_allocate',
    'resolve_group_id',
]

logger = logging.getLogger(__name__)


def resolve_group_id(
    envelope: MessageEnvelope[Any],
    partition_by: PartitionKeyExtractor | None,
) -> GroupId | None:
    """Resolve partition key without allocating: ``envelope.group_id`` → ``partition_by(payload)`` → ``None``.

    Keyless messages bypass sequencing (unordered). Split from allocation so scheduled dispatch can resolve
    the key without allocating — allocation is deferred to promotion so delayed messages sort after queued
    siblings.
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
