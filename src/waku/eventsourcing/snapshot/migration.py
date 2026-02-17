from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Any

from waku.eventsourcing.exceptions import SnapshotMigrationChainError
from waku.eventsourcing.snapshot.interfaces import Snapshot

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.stream import StreamId

__all__ = [
    'ISnapshotMigration',
    'SnapshotMigrationChain',
    'migrate_snapshot_or_discard',
]

logger = logging.getLogger(__name__)


class ISnapshotMigration(abc.ABC):
    from_version: int
    to_version: int

    @abc.abstractmethod
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]: ...


class SnapshotMigrationChain:
    __slots__ = ('_migrations',)

    def __init__(self, migrations: Sequence[ISnapshotMigration]) -> None:
        sorted_migrations = sorted(migrations, key=lambda m: m.from_version)
        seen: set[int] = set()
        prev_to: int | None = None
        for m in sorted_migrations:
            if m.from_version < 1:
                msg = f'Invalid from_version {m.from_version}: must be >= 1'
                raise SnapshotMigrationChainError(msg)
            if m.to_version <= m.from_version:
                msg = f'Invalid migration: to_version {m.to_version} must be > from_version {m.from_version}'
                raise SnapshotMigrationChainError(msg)
            if m.from_version in seen:
                msg = f'Duplicate snapshot migration at from_version {m.from_version}'
                raise SnapshotMigrationChainError(msg)
            if prev_to is not None and m.from_version != prev_to:
                msg = (
                    f'Gap in snapshot migration chain: '
                    f'migration to version {prev_to} is not followed by migration from version {prev_to} '
                    f'(found from_version {m.from_version})'
                )
                raise SnapshotMigrationChainError(msg)
            seen.add(m.from_version)
            prev_to = m.to_version
        self._migrations = tuple(sorted_migrations)

    @property
    def migrations(self) -> tuple[ISnapshotMigration, ...]:
        return self._migrations

    def migrate(self, state: dict[str, Any], from_version: int) -> tuple[dict[str, Any], int]:
        current = from_version
        for m in self._migrations:
            if m.from_version == current:
                state = m.migrate(state)
                current = m.to_version
        return state, current


def migrate_snapshot_or_discard(
    chain: SnapshotMigrationChain,
    snapshot: Snapshot,
    target_version: int,
    stream_id: StreamId,
) -> Snapshot | None:
    migrated_state, reached = chain.migrate(snapshot.state, snapshot.schema_version)
    if reached != target_version:
        logger.warning(
            'Snapshot schema version %d does not match expected %d for stream %s. '
            'No complete migration path. Discarding snapshot and replaying from events.',
            snapshot.schema_version,
            target_version,
            stream_id,
        )
        return None
    return Snapshot(
        stream_id=snapshot.stream_id,
        state=migrated_state,
        version=snapshot.version,
        state_type=snapshot.state_type,
        schema_version=reached,
    )


_EMPTY_CHAIN = SnapshotMigrationChain(())
