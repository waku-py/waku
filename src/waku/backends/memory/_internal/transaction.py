from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable  # noqa: TC003  # Dishka evaluates provider return annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TypeVar

import anyio

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterState
from waku.backends.memory._internal.inbox import InMemoryInboxState
from waku.backends.memory._internal.outbox import InMemoryOutboxState
from waku.backends.memory._internal.sequence import InMemorySequenceState
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointState
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotState
from waku.eventsourcing.store.in_memory import InMemoryEventStoreState

__all__ = [
    'InMemoryCommittedState',
    'InMemoryTransactionWorkspace',
    'provide_in_memory_transaction_workspace',
]

_SelectedStateT = TypeVar('_SelectedStateT')


@dataclass
class _InMemoryBackendState:
    outbox: InMemoryOutboxState = field(default_factory=InMemoryOutboxState)
    inbox: InMemoryInboxState = field(default_factory=InMemoryInboxState)
    dead_letters: InMemoryDeadLetterState = field(default_factory=InMemoryDeadLetterState)
    sequence: InMemorySequenceState = field(default_factory=InMemorySequenceState)
    events: InMemoryEventStoreState = field(default_factory=InMemoryEventStoreState)
    snapshots: InMemorySnapshotState = field(default_factory=InMemorySnapshotState)
    checkpoints: InMemoryCheckpointState = field(default_factory=InMemoryCheckpointState)


class _WorkspaceLifecycle(Enum):
    NEW = auto()
    ACTIVE = auto()
    COMMITTED = auto()
    ROLLED_BACK = auto()


class InMemoryWorkspaceAccessor:
    """Select staged state only while the owning workspace remains active."""

    __slots__ = ('_workspace',)

    def __init__(self, workspace: InMemoryTransactionWorkspace) -> None:
        self._workspace = workspace

    def ensure_active(self) -> None:
        self._workspace.active_state()

    def select(self, selector: Callable[[_InMemoryBackendState], _SelectedStateT]) -> _SelectedStateT:
        return selector(self._workspace.active_state())


class InMemoryCommittedState:
    """App-lifetime committed state serialized by explicit workspace borrower tokens."""

    __slots__ = ('_borrower', '_limiter', '_state')

    def __init__(self) -> None:
        self._borrower: object | None = None
        self._limiter = anyio.CapacityLimiter(1)
        self._state = _InMemoryBackendState()

    async def begin(self, borrower: object) -> _InMemoryBackendState:
        await self._limiter.acquire_on_behalf_of(borrower)
        try:
            staged = copy.deepcopy(self._state)
        except BaseException:
            self._limiter.release_on_behalf_of(borrower)
            raise
        self._borrower = borrower
        return staged

    def publish(self, borrower: object, staged: _InMemoryBackendState) -> None:
        self._ensure_borrower(borrower)
        self._state = copy.deepcopy(staged)

    def discard(self, borrower: object) -> None:
        self._ensure_borrower(borrower)

    def release(self, borrower: object) -> None:
        self._ensure_borrower(borrower)
        self._limiter.release_on_behalf_of(borrower)
        self._borrower = None

    def _ensure_borrower(self, borrower: object) -> None:
        if self._borrower is not borrower:
            msg = 'In-memory committed state is not held by this workspace'
            raise RuntimeError(msg)


class InMemoryTransactionWorkspace:
    """One token-serialized transaction over a staged snapshot of committed state."""

    __slots__ = ('_accessor', '_borrower', '_committed', '_lifecycle', '_state')

    def __init__(self, committed: InMemoryCommittedState) -> None:
        self._borrower = object()
        self._committed = committed
        self._lifecycle = _WorkspaceLifecycle.NEW
        self._state: _InMemoryBackendState | None = None
        self._accessor = InMemoryWorkspaceAccessor(self)

    @property
    def accessor(self) -> InMemoryWorkspaceAccessor:
        return self._accessor

    async def start(self) -> None:
        if self._lifecycle is not _WorkspaceLifecycle.NEW:
            msg = 'In-memory transaction workspace has already started'
            raise RuntimeError(msg)
        self._state = await self._committed.begin(self._borrower)
        self._lifecycle = _WorkspaceLifecycle.ACTIVE

    async def commit(self) -> None:
        staged = self.active_state()
        self._committed.publish(self._borrower, staged)
        self._finish(_WorkspaceLifecycle.COMMITTED)

    async def rollback(self) -> None:
        self.active_state()
        self._committed.discard(self._borrower)
        self._finish(_WorkspaceLifecycle.ROLLED_BACK)

    async def teardown(self) -> None:
        if self._lifecycle is _WorkspaceLifecycle.ACTIVE:
            await self.rollback()

    def active_state(self) -> _InMemoryBackendState:
        if self._lifecycle is _WorkspaceLifecycle.NEW:
            msg = 'In-memory transaction workspace has not started'
            raise RuntimeError(msg)
        if self._lifecycle is not _WorkspaceLifecycle.ACTIVE:
            msg = 'In-memory transaction workspace already completed'
            raise RuntimeError(msg)
        if self._state is None:  # pragma: no cover - lifecycle state and staged state change together
            msg = 'In-memory transaction workspace has no active state'
            raise RuntimeError(msg)
        return self._state

    def _finish(self, lifecycle: _WorkspaceLifecycle) -> None:
        self._state = None
        self._lifecycle = lifecycle
        self._committed.release(self._borrower)


async def provide_in_memory_transaction_workspace(
    committed: InMemoryCommittedState,
) -> AsyncIterator[InMemoryTransactionWorkspace]:
    workspace = InMemoryTransactionWorkspace(committed)
    try:
        await workspace.start()
        yield workspace
    finally:
        with anyio.CancelScope(shield=True):
            await workspace.teardown()
