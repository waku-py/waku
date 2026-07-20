from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import DuplicateIdempotencyKeyError, PartialDuplicateAppendError

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from waku.eventsourcing.contracts.stream import StreamId

__all__ = [
    'IdempotencyVerdict',
    'classify_idempotency',
]


class IdempotencyVerdict(Enum):
    """Outcome of classifying an append batch against a stream's stored idempotency keys."""

    PROCEED = auto()
    IDEMPOTENT_REPLAY = auto()


def classify_idempotency(
    stream_id: StreamId,
    batch_keys: Sequence[str],
    existing_keys: Collection[str],
) -> IdempotencyVerdict:
    """Classify an append batch against the stream's already-stored idempotency keys.

    Pure decision shared by every backend: the existence lookup is the backend's own and its result is
    passed in as ``existing_keys``. A backend layers its archived guard around the ``IDEMPOTENT_REPLAY``
    verdict, so the classifier runs first — a malformed or conflicting batch is reported before archival.

    Args:
        stream_id: The stream being appended to.
        batch_keys: The batch's idempotency keys in order; a key repeated within the batch is an error.
        existing_keys: The batch keys already stored on the stream (the backend's lookup result).

    Returns:
        ``PROCEED`` when no batch key was seen before; ``IDEMPOTENT_REPLAY`` when every batch key already
        exists (the caller returns the stored version).

    Raises:
        DuplicateIdempotencyKeyError: The batch repeats an idempotency key within itself.
        PartialDuplicateAppendError: Only some batch keys already exist — a non-idempotent overlap.
    """
    unique_keys = set(batch_keys)
    if len(unique_keys) != len(batch_keys):
        raise DuplicateIdempotencyKeyError(stream_id, reason='duplicate keys within batch')

    found = unique_keys & set(existing_keys)
    if not found:
        return IdempotencyVerdict.PROCEED
    if found == unique_keys:
        return IdempotencyVerdict.IDEMPOTENT_REPLAY
    raise PartialDuplicateAppendError(stream_id, len(found), len(batch_keys))
