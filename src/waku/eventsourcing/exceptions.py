from __future__ import annotations

from waku.exceptions import WakuError

__all__ = [
    'AggregateNotFoundError',
    'ConcurrencyConflictError',
    'DuplicateEventTypeError',
    'EventSourcingError',
    'ProjectionError',
    'ProjectionStoppedError',
    'RegistryFrozenError',
    'RetryExhaustedError',
    'SnapshotTypeMismatchError',
    'StreamNotFoundError',
    'UnknownEventTypeError',
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


class UnknownEventTypeError(EventSourcingError):
    def __init__(self, event_type_name: str) -> None:
        self.event_type_name = event_type_name
        super().__init__(f'Unknown event type: {event_type_name!r}')


class DuplicateEventTypeError(EventSourcingError):
    def __init__(self, event_type_name: str) -> None:
        self.event_type_name = event_type_name
        super().__init__(f'Event type {event_type_name!r} is already registered')


class SnapshotTypeMismatchError(EventSourcingError):
    def __init__(self, stream_id: str, expected_type: str, actual_type: str) -> None:
        self.stream_id = stream_id
        self.expected_type = expected_type
        self.actual_type = actual_type
        super().__init__(
            f'Snapshot type mismatch on stream {stream_id!r}: expected {expected_type!r}, got {actual_type!r}'
        )


class RegistryFrozenError(EventSourcingError):
    def __init__(self) -> None:
        super().__init__('Cannot register event types after registry is frozen')


class ProjectionError(EventSourcingError):
    pass


class ProjectionStoppedError(ProjectionError):
    def __init__(self, projection_name: str, cause: Exception) -> None:
        self.projection_name = projection_name
        self.cause = cause
        super().__init__(f'Projection {projection_name!r} stopped due to error: {cause}')


class RetryExhaustedError(ProjectionError):
    def __init__(self, projection_name: str, attempts: int, cause: Exception) -> None:
        self.projection_name = projection_name
        self.attempts = attempts
        self.cause = cause
        super().__init__(f'Projection {projection_name!r} exhausted {attempts} retry attempts: {cause}')
