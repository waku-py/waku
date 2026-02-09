from __future__ import annotations

from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists
from waku.eventsourcing.exceptions import ConcurrencyConflictError

__all__ = ['check_expected_version']


def check_expected_version(
    stream_id: str,
    expected: Exact | NoStream | StreamExists | AnyVersion,
    current_version: int,
    *,
    exists: bool,
) -> None:
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
