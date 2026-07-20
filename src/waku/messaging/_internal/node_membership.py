from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final, Never

import anyio
from typing_extensions import override

from waku._internal.node import INodeRegistry, NodeIdentity, NodeRegistryConfig
from waku._internal.transaction import Commit, TransactionDecision, run_committed
from waku.extensions import AfterApplicationInit, OnApplicationShutdown, OnContainerBuilt
from waku.messaging._internal.polling_agent import FixedPace, Placement, PollingAgent, Throttle

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.application import WakuApplication

__all__ = [
    'NodeMembershipAgent',
    'NodeMembershipLifecycleExtension',
]

logger = logging.getLogger(__name__)

# No capability vocabulary exists yet: the column is written from day one so adding a capability later
# needs no migration, but declaring names nobody reads would invent a contract this slice cannot honour.
_CAPABILITIES: Final[frozenset[str]] = frozenset()


class NodeMembershipAgent(PollingAgent):
    """Keeps this process's membership row alive and sweeps peers that stopped proving liveness.

    Runs on EVERY node and is never leader-gated: a leader-gated heartbeat would leave every follower
    looking dead to the liveness oracle, and the rows those followers are actively working on would be
    handed to someone else. A node registers because it exists, not because it won anything.

    Staleness is never computed here — ``INodeRegistry`` evaluates it with the store's clock, so this
    agent only supplies the configured thresholds and its own identity.

    **Failure policy — a failed membership transaction is logged at ERROR and retried on the next tick;
    the loop never dies on a transaction failure.** Consumers may rely on this. Control flow still ends
    it by design: a cancellation reaching the tick, however it is carried, stops the agent like any
    other task. A stopped heartbeat loop is harmful in the wrong
    direction: the process keeps running and keeps owning durable rows while ``last_heartbeat`` freezes,
    so a fencing peer reclaims a *healthy* node's in-flight work — the exact failure this substrate
    exists to prevent — and the ``heartbeat() is False -> re-register`` self-healing below becomes
    unreachable. Retrying is strictly safer than stopping because a heartbeat is idempotent and O(1):
    if the store is genuinely unreachable, the store-side truth (a ``last_heartbeat`` going stale)
    already bounds the damage, and an owner-guarded finalize rejects a stale owner's write, so a node
    that loses its database and is reclaimed cannot corrupt the outcome. Escalating to application
    shutdown was rejected — truthful, but it converts a transient database blip into a process kill.
    """

    placement = Placement.PER_POD
    retries_after_fatal = True

    __slots__ = ('_config', '_container', '_evict_throttle', '_identity', '_settling')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        identity: NodeIdentity,
        config: NodeRegistryConfig,
    ) -> None:
        self._container = container
        self._identity = identity
        self._config = config
        self._evict_throttle = Throttle(config.evict_interval.total_seconds())
        self._settling = True
        super().__init__(stop_timeout=config.stop_timeout)

    async def register(self) -> None:
        """Commit this node's membership row.

        Must complete before anything claims a durable row: a row owned by a node the registry has
        never heard of is indistinguishable from a row owned by a dead one.
        """
        await run_committed(self._container, self._register)

    async def deregister(self) -> None:
        """Remove this node's row so its durable rows become reclaimable with no wait for the TTL."""
        await run_committed(self._container, self._deregister)

    @override
    def _make_pace(self) -> FixedPace:
        # Fixed, not adaptive: the heartbeat cadence is the denominator of the staleness ratio, so it
        # must not stretch just because the node is idle.
        return FixedPace(self._config.heartbeat_interval.total_seconds())

    @override
    async def _tick(self) -> int:
        if self._settling:
            # The cycle that starts at boot does nothing: registration stamped ``last_heartbeat``
            # moments earlier, so proving liveness again would be a provably duplicate write. Matches
            # Wolverine, which likewise delays its first health check rather than running one at startup.
            self._settling = False
            return 0
        if not await run_committed(self._container, self._heartbeat):
            logger.warning(
                'Node %s was evicted from the registry while still alive; re-registering',
                self._identity.node_id,
            )
            await self.register()
        return await self._maybe_evict_stale()

    async def _maybe_evict_stale(self) -> int:
        if not self._evict_throttle.ready(time.monotonic()):
            return 0
        evicted = await run_committed(self._container, self._evict_stale)
        if evicted > 0:
            logger.info('Evicted %d node(s) silent for longer than %s', evicted, self._config.stale_after)
        return evicted

    async def _register(self, scope: AsyncContainer) -> TransactionDecision[None, Never]:
        registry = await scope.get(INodeRegistry)
        await registry.register(self._identity, capabilities=_CAPABILITIES)
        return Commit(None)

    async def _heartbeat(self, scope: AsyncContainer) -> TransactionDecision[bool, Never]:
        registry = await scope.get(INodeRegistry)
        return Commit(await registry.heartbeat(self._identity.node_id))

    async def _deregister(self, scope: AsyncContainer) -> TransactionDecision[None, Never]:
        registry = await scope.get(INodeRegistry)
        await registry.deregister(self._identity.node_id)
        return Commit(None)

    async def _evict_stale(self, scope: AsyncContainer) -> TransactionDecision[int, Never]:
        registry = await scope.get(INodeRegistry)
        evicted = await registry.evict_stale(stale_after=self._config.stale_after, keep=self._identity.node_id)
        return Commit(evicted)


class NodeMembershipLifecycleExtension(OnContainerBuilt, AfterApplicationInit, OnApplicationShutdown):
    """Owns this process's membership for as long as durable rows can exist.

    Registration runs at ``on_container_built`` — one lifecycle phase EARLIER than every claimer's
    ``after_app_init`` start — so the register-before-claim precondition holds by construction rather
    than by this extension's position in a list. Position still matters at the other end: registered
    first means shut down last, so this node stays a member until every claimer has stopped.

    Because that position must be claimed before the aggregated handler map exists, the wiring
    authority arms the extension afterwards via ``set_required``; unarmed it is inert at every phase.
    The default is armed: registering a node that turns out to write nothing costs one row, while
    skipping one that does write is the reclaim-healthy-work failure this substrate exists to prevent.
    """

    __slots__ = ('_agent', '_required')

    def __init__(self) -> None:
        self._agent: NodeMembershipAgent | None = None
        self._required = True

    def set_required(self, *, required: bool) -> None:
        """Declare whether this process can write durable rows, and therefore owes a membership row."""
        self._required = required

    @override
    async def on_container_built(self, app: WakuApplication) -> None:
        if not self._required:
            return
        agent = NodeMembershipAgent(
            container=app.container,
            identity=await app.container.get(NodeIdentity),
            config=await app.container.get(NodeRegistryConfig),
        )
        await agent.register()
        self._agent = agent

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        if self._agent is not None:
            await self._agent.start()

    @override
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        if self._agent is None:
            return
        try:
            await self._agent.stop()
        finally:
            # Shielded and unconditional: stopping the agent is the widest await in this path and can
            # both raise (a fatal tick killed the loop) and absorb a cancellation. Either way this node
            # must still hand its rows back immediately, otherwise they stay owned by a process that no
            # longer exists until the stale timeout expires.
            with anyio.CancelScope(shield=True):
                await self._agent.deregister()
