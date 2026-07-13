from __future__ import annotations

from typing import assert_never

from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError

__all__ = ['check_expected_version']


def check_expected_version(
    stream_id: StreamId,
    expected: Exact | NoStream | StreamExists | AnyVersion,
    current_version: int,
    *,
    exists: bool,
) -> None:
    """Assert the stream matches the expected-version precondition before an append.

    Args:
        stream_id: The stream being appended to.
        expected: The optimistic-concurrency precondition to enforce.
        current_version: The stream's current version.
        exists: Whether the stream already exists.

    Raises:
        ConcurrencyConflictError: The precondition does not hold.
    """
    match expected:
        case AnyVersion():
            return
        case NoStream():
            if exists:
                raise ConcurrencyConflictError(stream_id, -1, current_version)
        case StreamExists():
            if not exists:
                raise ConcurrencyConflictError(stream_id, 0, -1)
        case Exact(version=v):
            if v != current_version:
                raise ConcurrencyConflictError(stream_id, v, current_version)
        case _:
            assert_never(expected)
