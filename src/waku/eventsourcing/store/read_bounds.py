from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import StreamPosition

__all__ = ['check_read_bounds']


def check_read_bounds(start: int | StreamPosition, count: int | None) -> None:
    """Reject out-of-range ``read_stream`` bounds before a backend touches its storage.

    Pure precondition shared by every backend so the read contract cannot diverge across adapters:
    a negative ``start`` offset or a negative ``count`` limit is a caller error, not a silently
    coerced empty read (in-memory would tail-slice, SQL would emit an invalid ``LIMIT``).

    Args:
        start: The first position to read: the ``StreamPosition`` sentinel or a ``>= 0`` offset.
        count: The maximum number of events to return: ``None`` (unlimited), ``0`` (empty), or ``> 0``.

    Raises:
        ValueError: ``start`` is a negative offset, or ``count`` is a negative limit.
    """
    if isinstance(start, int) and start < 0:
        msg = f'read_stream start must be a non-negative offset or a StreamPosition; got {start}'
        raise ValueError(msg)
    if count is not None and count < 0:
        msg = f'read_stream count must be None or a non-negative limit; got {count}'
        raise ValueError(msg)
