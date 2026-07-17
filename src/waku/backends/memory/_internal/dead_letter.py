from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeGuard

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterStatus, validate_requested_lease

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta
    from uuid import UUID

    from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor
    from waku.messaging.errors.dead_letter import DeadLetterQuery

__all__ = ['InMemoryDeadLetterStore']


@dataclasses.dataclass
class InMemoryDeadLetterState:
    """Mutable state backing one in-memory dead-letter store view."""

    entries: dict[UUID, DeadLetterEntry] = dataclasses.field(default_factory=dict)


class _InMemoryDeadLetterStoreOperations(IDeadLetterStore):
    """Faithful in-memory ``IDeadLetterStore`` mirroring ``SqlAlchemyDeadLetterStore``'s observable semantics.

    The memory backend's dead-letter facet: ``created_at`` is stamped at save time (mirroring the
    server default), ``fetch``/``claim_replayable`` are oldest-first, ``query`` is newest-first with
    the same filter set. Not thread-safe.
    """

    __slots__ = ()

    def _get_state(self) -> InMemoryDeadLetterState:
        msg = 'subclasses must provide dead-letter state'
        raise NotImplementedError(msg)

    @property
    def entries(self) -> dict[UUID, DeadLetterEntry]:
        return self._get_state().entries

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        if entry.created_at is None:
            entry = dataclasses.replace(entry, created_at=datetime.now(tz=UTC))
        self.entries[entry.id] = entry

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        return self._oldest_first()[:batch_size]

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        try:
            return self.entries[entry_id]
        except KeyError:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg) from None

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        matched = [entry for entry in self.entries.values() if self._matches(entry, filters)]
        matched.sort(key=self._created_at_key, reverse=True)
        return matched[filters.offset : filters.offset + filters.limit]

    @override
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        for entry in self._oldest_first():
            eligible_status = entry.status is DeadLetterStatus.PENDING or (
                entry.status is DeadLetterStatus.REPLAY_FAILED and entry.replay_count < max_replay_count
            )
            if eligible_status and _lease_is_claimable(entry, now):
                return self._set_claim(entry, owner_id, lease_expires_at)
        return None

    @override
    async def claim_replay(
        self,
        entry_id: UUID,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        entry = self.entries.get(entry_id)
        if entry is None or entry.status is DeadLetterStatus.REPLAYED or not _lease_is_claimable(entry, now):
            return None
        return self._set_claim(entry, owner_id, lease_expires_at)

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        validate_requested_lease(now, lease_expires_at)
        entry = self.entries.get(entry_id)
        if not _has_live_owner(entry, owner_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(entry, replay_lease_expires_at=lease_expires_at)
        return True

    @override
    async def mark_replayed(self, entry_id: UUID, *, owner_id: str, now: datetime) -> bool:
        entry = self.entries.get(entry_id)
        if not _has_live_owner(entry, owner_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(
            entry,
            status=DeadLetterStatus.REPLAYED,
            replay_owner_id=None,
            replay_lease_expires_at=None,
        )
        return True

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str, *, owner_id: str, now: datetime) -> bool:
        entry = self.entries.get(entry_id)
        if not _has_live_owner(entry, owner_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(
            entry,
            status=DeadLetterStatus.REPLAY_FAILED,
            replay_count=entry.replay_count + 1,
            error_message=error,
            replay_owner_id=None,
            replay_lease_expires_at=None,
        )
        return True

    @override
    async def delete(self, entry_id: UUID) -> None:
        self.entries.pop(entry_id, None)

    @override
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:
        cutoff = now - older_than
        stale = [
            entry_id
            for entry_id, entry in self.entries.items()
            if entry.created_at is not None and entry.created_at < cutoff and _lease_is_claimable(entry, now)
        ]
        for entry_id in stale:
            del self.entries[entry_id]
        return len(stale)

    def _set_claim(self, entry: DeadLetterEntry, owner_id: str, lease_expires_at: datetime) -> DeadLetterEntry:
        claimed = dataclasses.replace(
            entry,
            replay_owner_id=owner_id,
            replay_lease_expires_at=lease_expires_at,
        )
        self.entries[entry.id] = claimed
        return claimed

    def _oldest_first(self) -> list[DeadLetterEntry]:
        return sorted(self.entries.values(), key=self._created_at_key)

    @staticmethod
    def _created_at_key(entry: DeadLetterEntry) -> datetime:
        return entry.created_at if entry.created_at is not None else datetime.now(tz=UTC)

    @staticmethod
    def _matches(entry: DeadLetterEntry, filters: DeadLetterQuery) -> bool:
        conditions = (
            filters.status is None or entry.status is filters.status,
            filters.message_type is None or entry.message_type == filters.message_type,
            filters.destination is None or entry.destination == filters.destination,
            filters.created_after is None
            or (entry.created_at is not None and entry.created_at >= filters.created_after),
            filters.created_before is None
            or (entry.created_at is not None and entry.created_at < filters.created_before),
        )
        return all(conditions)


class InMemoryDeadLetterStore(_InMemoryDeadLetterStoreOperations):
    __slots__ = ('_state',)

    def __init__(self) -> None:
        self._state = InMemoryDeadLetterState()

    @override
    def _get_state(self) -> InMemoryDeadLetterState:
        return self._state


class WorkspaceDeadLetterStore(_InMemoryDeadLetterStoreOperations):
    __slots__ = ('_accessor',)

    def __init__(self, accessor: InMemoryWorkspaceAccessor) -> None:
        accessor.ensure_active()
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemoryDeadLetterState:
        return self._accessor.select(lambda state: state.dead_letters)


def _lease_is_claimable(entry: DeadLetterEntry, now: datetime) -> bool:
    return entry.replay_lease_expires_at is None or entry.replay_lease_expires_at <= now


def _has_live_owner(entry: DeadLetterEntry | None, owner_id: str, now: datetime) -> TypeGuard[DeadLetterEntry]:
    return (
        entry is not None
        and entry.replay_owner_id == owner_id
        and entry.replay_lease_expires_at is not None
        and entry.replay_lease_expires_at > now
    )
