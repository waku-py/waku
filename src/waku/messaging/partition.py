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
]

logger = logging.getLogger(__name__)

# Resolves a message to its partition key (``group_id``). ``None`` means the message is keyless:
# it bypasses sequencing and is dispatched in parallel (no per-group ordering guarantee).
PartitionKeyExtractor: TypeAlias = 'Callable[[IMessage], str | None]'


class ISequenceAllocator(abc.ABC):
    @abc.abstractmethod
    async def allocate(self, group_id: GroupId) -> int:
        """Atomically allocate the next monotonic sequence number for ``group_id``.

        Implementations MUST run within the same transaction as the entry insertion so the
        ``(group_id, sequence_number)`` written to the outbox/inbox row is co-committed with the
        allocation. The per-group row lock + MVCC then make per-group FIFO physically correct
        (verified in ``.research/sequence_rowlock_mre.md``).
        """


async def resolve_and_allocate(
    envelope: MessageEnvelope[Any],
    partition_by: PartitionKeyExtractor | None,
    scope: AsyncContainer,
) -> tuple[GroupId | None, int | None]:
    """Resolve a message's partition key and, if keyed, allocate its next sequence number.

    Precedence: explicit ``envelope.group_id`` -> ``partition_by(payload)`` -> ``None``. A keyless
    message (no determinable key) bypasses sequencing entirely and is dispatched unordered (Decision
    B: keyless = parallel, order NOT guaranteed). The bypass is logged at debug so a misconfigured
    ``partition_by`` returning ``None`` for everything is observable rather than silent.

    Shared by ``ExternalEndpoint`` and ``DurableLocalQueueEndpoint`` so the
    precedence and the allocate-only-when-keyed rule live in exactly one place.
    """
    raw_group_id = envelope.group_id
    if raw_group_id is None and partition_by is not None:
        raw_group_id = partition_by(envelope.payload)
    if raw_group_id is None:
        logger.debug('Keyless message %s bypassing sequencing (order not guaranteed)', envelope.message_type)
        return None, None
    group_id = GroupId(raw_group_id)
    allocator = await scope.get(ISequenceAllocator)
    return group_id, await allocator.allocate(group_id)
