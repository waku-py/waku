from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING, TypeGuard

from typing_extensions import override

from waku._internal.clock import utc_now
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterStatus, validate_requested_lease

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku._internal.node import NodeId
    from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor
    from waku.messaging.errors.dead_letter import DeadLetterQuery, ReplayClaimId

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
            entry = dataclasses.replace(entry, created_at=utc_now())
        # Serialize-in isolation: persist a snapshot so a caller mutating payload/metadata after
        # save never rewrites the stored row (the SQL peer serializes to JSONB on execute).
        self.entries[entry.id] = copy.deepcopy(entry)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        return [self._snapshot(entry) for entry in self._oldest_first()[:batch_size]]

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        try:
            return self._snapshot(self.entries[entry_id])
        except KeyError:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg) from None

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        matched = [entry for entry in self.entries.values() if self._matches(entry, filters)]
        matched.sort(key=self._created_at_key, reverse=True)
        return [self._snapshot(entry) for entry in matched[filters.offset : filters.offset + filters.limit]]

    @override
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        for entry in self._oldest_first():
            eligible_status = entry.status is DeadLetterStatus.PENDING or (
                entry.status is DeadLetterStatus.REPLAY_FAILED and entry.replay_count < max_replay_count
            )
            if eligible_status and _lease_is_claimable(entry, now):
                return self._set_claim(entry, owner_id, claim_id, lease_expires_at)
        return None

    @override
    async def claim_replay(
        self,
        entry_id: UUID,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        entry = self.entries.get(entry_id)
        if entry is None or entry.status is DeadLetterStatus.REPLAYED or not _lease_is_claimable(entry, now):
            return None
        return self._set_claim(entry, owner_id, claim_id, lease_expires_at)

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        validate_requested_lease(now, lease_expires_at)
        entry = self.entries.get(entry_id)
        if not _has_live_claim(entry, claim_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(entry, replay_lease_expires_at=lease_expires_at)
        return True

    @override
    async def mark_replayed(self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        entry = self.entries.get(entry_id)
        if not _has_live_claim(entry, claim_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(
            entry,
            status=DeadLetterStatus.REPLAYED,
            replay_owner_id=None,
            replay_lease_expires_at=None,
            replay_claim_id=None,
        )
        return True

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        entry = self.entries.get(entry_id)
        if not _has_live_claim(entry, claim_id, now):
            return False
        self.entries[entry_id] = dataclasses.replace(
            entry,
            status=DeadLetterStatus.REPLAY_FAILED,
            replay_count=entry.replay_count + 1,
            error_message=error,
            replay_owner_id=None,
            replay_lease_expires_at=None,
            replay_claim_id=None,
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

    def _set_claim(
        self,
        entry: DeadLetterEntry,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry:
        claimed = dataclasses.replace(
            entry,
            replay_owner_id=owner_id,
            replay_lease_expires_at=lease_expires_at,
            replay_claim_id=claim_id,
        )
        self.entries[entry.id] = claimed
        return self._snapshot(claimed)

    @staticmethod
    def _snapshot(entry: DeadLetterEntry) -> DeadLetterEntry:
        # Deserialize-out isolation: every read return is a snapshot, so a caller mutating
        # payload/metadata never rewrites stored state (the SQL peer reads fresh objects per row).
        return copy.deepcopy(entry)

    def _oldest_first(self) -> list[DeadLetterEntry]:
        return sorted(self.entries.values(), key=self._created_at_key)

    @staticmethod
    def _created_at_key(entry: DeadLetterEntry) -> datetime:
        return entry.created_at if entry.created_at is not None else utc_now()

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


def _has_live_claim(
    entry: DeadLetterEntry | None,
    claim_id: ReplayClaimId,
    now: datetime,
) -> TypeGuard[DeadLetterEntry]:
    """The exclusion fence: this exact claim, still strictly live. Never keyed on the owner."""
    return (
        entry is not None
        and entry.replay_claim_id == claim_id
        and entry.replay_lease_expires_at is not None
        and entry.replay_lease_expires_at > now
    )
