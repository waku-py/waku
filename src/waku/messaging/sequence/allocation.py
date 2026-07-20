from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from waku.messaging.sequence.contracts import GroupId, ISequenceAllocator

__all__ = ['allocate_sequence_by_id']


async def allocate_sequence_by_id(
    id_group_pairs: Sequence[tuple[UUID, GroupId | None]],
    allocator: ISequenceAllocator,
) -> dict[UUID, int | None]:
    """Allocate one sequence value per distinct message id (all fan-out rows of a message share a position).

    Keyless rows (``group_id is None``) map to ``None`` — unsequenced. Inbox backends promoting a due
    scheduled batch call this so the allocate-once-per-message invariant lives in exactly one place.
    """
    sequence_by_id: dict[UUID, int | None] = {}
    for message_id, group_id in id_group_pairs:
        if message_id not in sequence_by_id:
            sequence_by_id[message_id] = await allocator.allocate(group_id) if group_id is not None else None
    return sequence_by_id
