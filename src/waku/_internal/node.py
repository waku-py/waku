from __future__ import annotations

import abc
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, NewType

from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

__all__ = [
    'DEFAULT_NODE_REGISTRY_CONFIG',
    'INodeRegistry',
    'NodeId',
    'NodeIdentity',
    'NodeRegistration',
    'NodeRegistryConfig',
]

# Follows the persisted-identity NewType guard convention (see waku.messaging.inbox.identifiers).
NodeId = NewType('NodeId', str)

# A merely-slow node must miss several heartbeats before it is declared dead, otherwise it is evicted,
# returns, and is evicted again — membership flaps and every consumer churns with it.
_MIN_STALE_HEARTBEAT_RATIO: Final = 3


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeIdentity:
    """This process's durable identity. Minted once at startup, never reused."""

    node_id: NodeId
    """uuid4 — THE identity, and the value stored in every owner column."""
    description: str
    """``'<hostname>:<pid>'`` or a configured label — diagnostics only, NEVER an identity.

    PIDs are reusable across restarts, so a restarted process can inherit a dead predecessor's
    label; ownership is therefore compared on ``node_id`` alone.
    """

    @classmethod
    def create(cls, description: str = '') -> NodeIdentity:
        """Mint a fresh identity; a blank *description* falls back to ``'<hostname>:<pid>'``."""
        return cls(
            node_id=NodeId(str(uuid.uuid4())),
            description=description or f'{socket.gethostname()}:{os.getpid()}',
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeRegistration:
    """A membership row as the registry holds it: who the node is, and when it last proved liveness."""

    node_id: NodeId
    description: str
    started_at: datetime
    last_heartbeat: datetime
    capabilities: frozenset[str]


class INodeRegistry(abc.ABC):
    """Cluster membership: which node instances exist, and which are alive.

    The single liveness oracle. Row age is never evidence of node death anywhere else — a consumer
    asks this port, or it does not ask at all.

    No method takes a ``now``: all staleness arithmetic is evaluated with the STORE's clock, never the
    caller's. This deliberately diverges from the ``delete_expired_dispatched(older_than, *, now)``
    convention, which compares a store-stamped column against an app-sampled cutoff over a retention
    window where inter-node clock skew is negligible. Liveness is decided on a seconds-scale
    threshold where that skew IS the failure mode, so admitting a caller clock would make one node's
    drift enough to declare another dead.

    Consumers that attribute work to a node reference ``node_id`` from their OWN table through their
    OWN port; they never add a method here and never add a column to the node row.
    """

    @abc.abstractmethod
    async def register(self, identity: NodeIdentity, *, capabilities: frozenset[str]) -> None:
        """Insert or reclaim this node's row, stamping ``started_at``/``last_heartbeat`` from the store clock.

        The caller owns the transaction; this method must not commit.
        """

    @abc.abstractmethod
    async def heartbeat(self, node_id: NodeId) -> bool:
        """Refresh ``last_heartbeat`` from the store clock.

        Returns False iff the row is gone — this node was evicted while alive and MUST re-register
        before it may claim or finalize anything.
        """

    @abc.abstractmethod
    async def deregister(self, node_id: NodeId) -> None:
        """Remove this node on clean shutdown so its rows are reclaimable immediately, with no TTL wait."""

    @abc.abstractmethod
    async def evict_stale(self, *, stale_after: timedelta, keep: NodeId) -> int:
        """Delete rows whose ``last_heartbeat`` is older than *stale_after* by the store clock, excluding *keep*.

        *keep* is an explicit parameter rather than an internally derived self-exclusion so the
        never-evict-self rule is part of the contract and testable through it. Returns the number of
        rows removed; idempotent and safe to race across nodes.
        """

    @abc.abstractmethod
    async def load_all(self) -> Sequence[NodeRegistration]:
        """Membership snapshot for diagnostics and future consumers.

        A recovery predicate MUST NOT be built on this read: reading membership and then reclaiming
        rows opens a window in which the read-live node dies, so that decision belongs in the single
        statement that performs the reclaim.
        """


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeRegistryConfig:
    heartbeat_interval: timedelta = timedelta(seconds=10)
    """How often a node proves it is alive."""
    stale_after: timedelta = timedelta(seconds=60)
    """Silence beyond which a node is declared dead; at least 3x ``heartbeat_interval``."""
    evict_interval: timedelta = timedelta(seconds=60)
    """How often a node sweeps the registry for dead peers."""
    stop_timeout: timedelta = timedelta(seconds=5)
    """Grace period for the membership agent's loop to finish its tick on shutdown.

    A tick is one small write, so anything slower is a wedged loop rather than a busy one.
    """

    def __post_init__(self) -> None:
        for field_name, value in (
            ('heartbeat_interval', self.heartbeat_interval),
            ('stale_after', self.stale_after),
            ('evict_interval', self.evict_interval),
            ('stop_timeout', self.stop_timeout),
        ):
            if value <= timedelta(0):
                msg = f'NodeRegistryConfig.{field_name} must be positive, got {value}'
                raise ImproperlyConfiguredError(msg)
        floor = _MIN_STALE_HEARTBEAT_RATIO * self.heartbeat_interval
        if self.stale_after < floor:
            msg = (
                f'NodeRegistryConfig.stale_after must be at least {_MIN_STALE_HEARTBEAT_RATIO}x '
                f'heartbeat_interval so a merely-slow node does not flap in and out of the cluster, '
                f'got {self.stale_after} with heartbeat_interval={self.heartbeat_interval} (minimum {floor})'
            )
            raise ImproperlyConfiguredError(msg)


# Module-level so backends can name it as a `register(...)` default without constructing one per call.
DEFAULT_NODE_REGISTRY_CONFIG: Final = NodeRegistryConfig()
