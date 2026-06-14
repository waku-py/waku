from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

__all__ = [
    'InboxEntry',
    'InboxStatus',
]


@enum.unique
class InboxStatus(enum.StrEnum):
    INCOMING = 'INCOMING'
    HANDLED = 'HANDLED'
    # Forward scaffolding for M3 scheduled-messages: a scheduled entry waits until
    # NOW() >= execution_time before the recovery worker promotes it to INCOMING.
    # M2b.1 never transitions into SCHEDULED — it is defined here so the `status`
    # column can take the value without a later migration.
    SCHEDULED = 'SCHEDULED'


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxEntry:
    id: UUID
    payload: dict[str, Any]
    message_type: str
    received_at: str
    # `destination` is the per-handler dedup discriminator: the handler FQN
    # `f'{handler_type.__module__}.{handler_type.__qualname__}'`. Together with `id`
    # it forms the composite primary key, so a fan-out message writes one row per
    # subscribed handler and each handler dedups independently. (`received_at` stays
    # the endpoint URI the message arrived on — observability metadata, not a dedup key.)
    destination: str
    status: InboxStatus = InboxStatus.INCOMING
    owner_id: str | None = None
    # M3 scheduled-messages populate execution_time and gate dispatch on
    # NOW() >= execution_time (SCHEDULED -> INCOMING when due). M2b.1 never writes a
    # non-None value — the column exists so the schema is stable when M3 lands.
    execution_time: datetime | None = None
    attempts: int = 0
    keep_until: datetime | None = None
    group_id: str | None = None
    sequence_number: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
