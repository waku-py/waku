from __future__ import annotations

from waku.exceptions import WakuError

__all__ = [
    'AggregateNotFoundError',
    'ConcurrencyConflictError',
    'EventSourcingError',
    'StreamNotFoundError',
]


class EventSourcingError(WakuError):
    pass


class StreamNotFoundError(EventSourcingError):
    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        super().__init__(f'Stream {stream_id!r} not found')


class ConcurrencyConflictError(EventSourcingError):
    def __init__(self, stream_id: str, expected_version: int, actual_version: int) -> None:
        self.stream_id = stream_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f'Concurrency conflict on stream {stream_id!r}: '
            f'expected version {expected_version}, actual {actual_version}'
        )


class AggregateNotFoundError(EventSourcingError):
    def __init__(self, aggregate_type: str, aggregate_id: str) -> None:
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        super().__init__(f'{aggregate_type} with id {aggregate_id!r} not found')
