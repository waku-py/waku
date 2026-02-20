from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import WakuError

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import StreamId

__all__ = [
    'AggregateNotFoundError',
    'ConcurrencyConflictError',
    'ConflictingEventTypeError',
    'DuplicateAggregateNameError',
    'DuplicateEventTypeError',
    'DuplicateIdempotencyKeyError',
    'EventSourcingConfigError',
    'EventSourcingError',
    'PartialDuplicateAppendError',
    'ProjectionError',
    'ProjectionStoppedError',
    'RegistryFrozenError',
    'SnapshotConfigNotFoundError',
    'SnapshotMigrationChainError',
    'SnapshotTypeMismatchError',
    'StreamNotFoundError',
    'StreamTooLargeError',
    'UnknownEventTypeError',
    'UpcasterChainError',
]


class EventSourcingError(WakuError):
    pass


class EventSourcingConfigError(EventSourcingError):
    pass


class StreamNotFoundError(EventSourcingError):
    def __init__(self, stream_id: StreamId) -> None:
        self.stream_id = stream_id
        super().__init__(f'Stream {stream_id} not found')


class ConcurrencyConflictError(EventSourcingError):
    def __init__(self, stream_id: StreamId, expected_version: int, actual_version: int) -> None:
        self.stream_id = stream_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f'Concurrency conflict on stream {stream_id}: expected version {expected_version}, actual {actual_version}'
        )


class AggregateNotFoundError(EventSourcingError):
    def __init__(self, aggregate_type: str, aggregate_id: str) -> None:
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        super().__init__(f'{aggregate_type} with id {aggregate_id!r} not found')


class DuplicateAggregateNameError(EventSourcingError):
    def __init__(self, aggregate_name: str, repositories: list[type]) -> None:
        self.aggregate_name = aggregate_name
        self.repositories = repositories
        repo_names = ', '.join(r.__name__ for r in repositories)
        super().__init__(f'Duplicate aggregate name {aggregate_name!r} used by multiple repositories: {repo_names}')


class UnknownEventTypeError(EventSourcingError):
    def __init__(self, event_type_name: str) -> None:
        self.event_type_name = event_type_name
        super().__init__(f'Unknown event type: {event_type_name!r}')


class DuplicateEventTypeError(EventSourcingError):
    def __init__(self, event_type_name: str) -> None:
        self.event_type_name = event_type_name
        super().__init__(f'Event type {event_type_name!r} is already registered')


class ConflictingEventTypeError(EventSourcingError):
    def __init__(
        self,
        event_type: type,
        existing_name: str,
        existing_version: int,
        attempted_name: str,
        attempted_version: int,
    ) -> None:
        self.event_type = event_type
        self.existing_name = existing_name
        self.existing_version = existing_version
        self.attempted_name = attempted_name
        self.attempted_version = attempted_version
        if existing_name != attempted_name:
            detail = f'name {existing_name!r} → {attempted_name!r}'
        else:
            detail = f'version v{existing_version} → v{attempted_version}'
        super().__init__(f'Conflicting registration for event type {event_type.__name__!r}: {detail}')


class SnapshotTypeMismatchError(EventSourcingError):
    def __init__(self, stream_id: StreamId, expected_type: str, actual_type: str) -> None:
        self.stream_id = stream_id
        self.expected_type = expected_type
        self.actual_type = actual_type
        super().__init__(
            f'Snapshot type mismatch on stream {stream_id}: expected {expected_type!r}, got {actual_type!r}'
        )


class StreamTooLargeError(EventSourcingError):
    def __init__(self, stream_id: StreamId, max_length: int) -> None:
        self.stream_id = stream_id
        self.max_length = max_length
        super().__init__(
            f'Stream {stream_id} exceeds maximum length of {max_length} events. '
            f'Configure snapshots to reduce stream replay size.'
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


class DuplicateIdempotencyKeyError(EventSourcingError):
    def __init__(self, stream_id: StreamId, *, reason: str) -> None:
        self.stream_id = stream_id
        self.reason = reason
        super().__init__(f'Duplicate idempotency keys ({reason}) on stream {stream_id}')


class PartialDuplicateAppendError(EventSourcingError):
    def __init__(self, stream_id: StreamId, existing_count: int, total_count: int) -> None:
        self.stream_id = stream_id
        self.existing_count = existing_count
        self.total_count = total_count
        super().__init__(
            f'Partial duplicate append on stream {stream_id}: '
            f'{existing_count} of {total_count} idempotency keys already exist'
        )


class SnapshotConfigNotFoundError(EventSourcingError):
    def __init__(self, aggregate_name: str) -> None:
        self.aggregate_name = aggregate_name
        super().__init__(
            f'No snapshot config found for aggregate {aggregate_name!r}. '
            f'Provide snapshot=SnapshotOptions(...) via bind_aggregate() or bind_decider().'
        )


class SnapshotMigrationChainError(EventSourcingError):
    pass


class UpcasterChainError(EventSourcingError):
    pass
