from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.node import INodeRegistry, NodeRegistration

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from waku._internal.node import NodeId, NodeIdentity
    from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor

__all__ = [
    'InMemoryNodeRegistry',
    'InMemoryNodeRegistryState',
    'WorkspaceNodeRegistry',
]


@dataclass
class InMemoryNodeRegistryState:
    """Mutable state backing one in-memory node-registry view."""

    nodes: dict[NodeId, NodeRegistration] = field(default_factory=dict)


class _InMemoryNodeRegistryOperations(INodeRegistry):
    """Faithful in-memory ``INodeRegistry`` mirroring ``SqlAlchemyNodeRegistry``'s observable semantics.

    ``self._now`` IS this backend's store clock — the exact counterpart of the SQL peer's ``now()``,
    read on both sides of every staleness comparison and never supplied by a caller. Injecting it is
    what lets a test age the cluster deterministically instead of sleeping.

    ``NodeRegistration`` is immutable throughout, so views need no defensive copying. Not thread-safe.
    """

    __slots__ = ('_now',)

    def __init__(self, now: Now = utc_now) -> None:
        self._now = now

    def _get_state(self) -> InMemoryNodeRegistryState:
        msg = 'subclasses must provide node registry state'
        raise NotImplementedError(msg)

    @property
    def nodes(self) -> dict[NodeId, NodeRegistration]:
        return self._get_state().nodes

    @override
    async def register(self, identity: NodeIdentity, *, capabilities: frozenset[str]) -> None:
        # One stamp for both columns mirrors the SQL upsert's single `now()`; re-registering an
        # existing id replaces the row outright, reclaiming it for the restarted instance.
        stamped_at = self._now()
        self.nodes[identity.node_id] = NodeRegistration(
            node_id=identity.node_id,
            description=identity.description,
            started_at=stamped_at,
            last_heartbeat=stamped_at,
            capabilities=capabilities,
        )

    @override
    async def heartbeat(self, node_id: NodeId) -> bool:
        current = self.nodes.get(node_id)
        if current is None:
            return False  # mirrors rowcount==0: the row is gone, this node was evicted while alive
        self.nodes[node_id] = replace(current, last_heartbeat=self._now())
        return True

    @override
    async def deregister(self, node_id: NodeId) -> None:
        self.nodes.pop(node_id, None)

    @override
    async def evict_stale(self, *, stale_after: timedelta, keep: NodeId) -> int:
        cutoff = self._now() - stale_after
        evicted = [
            node_id
            for node_id, registration in self.nodes.items()
            if node_id != keep and registration.last_heartbeat < cutoff
        ]
        for node_id in evicted:
            del self.nodes[node_id]
        return len(evicted)

    @override
    async def load_all(self) -> Sequence[NodeRegistration]:
        return list(self.nodes.values())


class InMemoryNodeRegistry(_InMemoryNodeRegistryOperations):
    """Standalone registry. Sharing one ``state`` across instances models contending nodes."""

    __slots__ = ('_state',)

    def __init__(self, *, state: InMemoryNodeRegistryState | None = None, now: Now = utc_now) -> None:
        super().__init__(now)
        self._state = state if state is not None else InMemoryNodeRegistryState()

    @override
    def _get_state(self) -> InMemoryNodeRegistryState:
        return self._state


class WorkspaceNodeRegistry(_InMemoryNodeRegistryOperations):
    __slots__ = ('_accessor',)

    def __init__(self, accessor: InMemoryWorkspaceAccessor, now: Now = utc_now) -> None:
        accessor.ensure_active()
        super().__init__(now)
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemoryNodeRegistryState:
        return self._accessor.select(lambda state: state.nodes)
