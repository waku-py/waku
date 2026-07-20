from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from waku._internal.node import NodeIdentity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from waku._internal.node import INodeRegistry, NodeId, NodeRegistration

__all__ = ['NodeRegistryBackend', 'NodeRegistryContract']

_STALE_AFTER = timedelta(seconds=60)
_BEYOND_STALE = timedelta(seconds=90)
_WITHIN_STALE = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class NodeRegistryBackend:
    make: Callable[[], INodeRegistry]
    """Build a distinct registry view over the ONE shared resource; two views model contending nodes."""
    advance: Callable[[timedelta], Awaitable[None]]
    """Move the store clock forward relative to every stored row, without sleeping.

    Backends whose store clock is injectable advance the clock; backends reading a server clock shift
    the stored timestamps back by the same amount — observationally identical, and the only way to age
    a row when the arithmetic is deliberately server-side.
    """


def _by_id(registrations: Sequence[NodeRegistration]) -> dict[NodeId, NodeRegistration]:
    return {registration.node_id: registration for registration in registrations}


class NodeRegistryContract:
    """Behavioral contract every :class:`~waku._internal.node.INodeRegistry` implementation must pass.

    Subclass in your backend's test suite and override the ``node_registry_backend`` fixture with a
    :class:`NodeRegistryBackend` over a fresh resource per test.

    Every assertion here is expressed through the port alone: no test reads a column, samples a wall
    clock, or asserts on a timestamp's absolute value. Staleness is driven exclusively through
    ``advance``, because the store — never the caller — owns the clock the predicate is evaluated on.
    """

    @pytest.fixture
    def node_registry_backend(self) -> NodeRegistryBackend:
        msg = 'override the node_registry_backend fixture with your backend provider'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_register_then_load_all_returns_node(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')

        await registry.register(identity, capabilities=frozenset())

        registrations = await registry.load_all()
        assert [r.node_id for r in registrations] == [identity.node_id]
        assert registrations[0].description == 'node-a'
        assert registrations[0].started_at.tzinfo is not None
        assert registrations[0].last_heartbeat.tzinfo is not None

    async def test_load_all_returns_every_registered_node(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        first = NodeIdentity.create('node-a')
        second = NodeIdentity.create('node-b')

        await registry.register(first, capabilities=frozenset())
        await node_registry_backend.make().register(second, capabilities=frozenset())

        assert set(_by_id(await registry.load_all())) == {first.node_id, second.node_id}

    async def test_reregister_same_id_refreshes_started_at(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')

        await registry.register(identity, capabilities=frozenset())
        await node_registry_backend.advance(_BEYOND_STALE)
        before = (await registry.load_all())[0]

        await registry.register(identity, capabilities=frozenset())

        after = await registry.load_all()
        assert len(after) == 1
        assert after[0].started_at > before.started_at
        assert after[0].last_heartbeat > before.last_heartbeat

    async def test_heartbeat_advances_last_heartbeat(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')
        await registry.register(identity, capabilities=frozenset())

        await node_registry_backend.advance(_WITHIN_STALE)
        before = (await registry.load_all())[0].last_heartbeat

        assert await registry.heartbeat(identity.node_id) is True

        assert (await registry.load_all())[0].last_heartbeat > before

    async def test_heartbeat_returns_false_for_unknown_node(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()

        assert await registry.heartbeat(NodeIdentity.create('never-registered').node_id) is False

    async def test_heartbeat_returns_false_after_eviction(self, node_registry_backend: NodeRegistryBackend) -> None:
        evicted = node_registry_backend.make()
        survivor = node_registry_backend.make()
        gone = NodeIdentity.create('node-a')
        alive = NodeIdentity.create('node-b')
        await evicted.register(gone, capabilities=frozenset())
        await survivor.register(alive, capabilities=frozenset())

        await node_registry_backend.advance(_BEYOND_STALE)
        assert await survivor.heartbeat(alive.node_id) is True
        assert await survivor.evict_stale(stale_after=_STALE_AFTER, keep=alive.node_id) == 1

        assert await evicted.heartbeat(gone.node_id) is False

    async def test_deregister_removes_immediately(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')
        await registry.register(identity, capabilities=frozenset())

        await registry.deregister(identity.node_id)

        assert await registry.load_all() == []
        assert await registry.heartbeat(identity.node_id) is False

    async def test_deregister_unknown_node_is_a_no_op(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')
        await registry.register(identity, capabilities=frozenset())

        await registry.deregister(NodeIdentity.create('never-registered').node_id)

        assert [r.node_id for r in await registry.load_all()] == [identity.node_id]

    async def test_evict_stale_removes_only_stale(self, node_registry_backend: NodeRegistryBackend) -> None:
        stale = node_registry_backend.make()
        fresh = node_registry_backend.make()
        silent = NodeIdentity.create('node-a')
        await stale.register(silent, capabilities=frozenset())

        await node_registry_backend.advance(_BEYOND_STALE)
        recent = NodeIdentity.create('node-b')
        await fresh.register(recent, capabilities=frozenset())

        assert await fresh.evict_stale(stale_after=_STALE_AFTER, keep=recent.node_id) == 1
        assert [r.node_id for r in await fresh.load_all()] == [recent.node_id]

    async def test_evict_stale_leaves_nodes_silent_for_less_than_the_threshold(
        self,
        node_registry_backend: NodeRegistryBackend,
    ) -> None:
        registry = node_registry_backend.make()
        quiet = NodeIdentity.create('node-a')
        keeper = NodeIdentity.create('node-b')
        await registry.register(quiet, capabilities=frozenset())
        await registry.register(keeper, capabilities=frozenset())

        await node_registry_backend.advance(_WITHIN_STALE)

        assert await registry.evict_stale(stale_after=_STALE_AFTER, keep=keeper.node_id) == 0
        assert set(_by_id(await registry.load_all())) == {quiet.node_id, keeper.node_id}

    async def test_evict_stale_excludes_a_node_at_exactly_the_threshold(
        self,
        node_registry_backend: NodeRegistryBackend,
    ) -> None:
        registry = node_registry_backend.make()
        boundary = NodeIdentity.create('node-a')
        keeper = NodeIdentity.create('node-b')
        await registry.register(boundary, capabilities=frozenset())
        await registry.register(keeper, capabilities=frozenset())

        await node_registry_backend.advance(_STALE_AFTER)

        # `last_heartbeat < now() - stale_after` is strict, so a node silent for EXACTLY the threshold
        # is still alive. The tie is the sharpest probe of whose clock decides: the cutoff and the
        # stamp must come from the same store clock to land on it, and any caller-sampled instant
        # substituted on either side lands microseconds off and evicts a live node.
        assert await registry.evict_stale(stale_after=_STALE_AFTER, keep=keeper.node_id) == 0
        assert set(_by_id(await registry.load_all())) == {boundary.node_id, keeper.node_id}

    async def test_evict_stale_never_evicts_keep(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        caller = NodeIdentity.create('node-a')
        peer = NodeIdentity.create('node-b')
        await registry.register(caller, capabilities=frozenset())
        await registry.register(peer, capabilities=frozenset())

        await node_registry_backend.advance(_BEYOND_STALE)

        # BOTH rows are stale by the store clock: the sweep is provably live (it removes the peer),
        # so the caller's survival is self-exclusion, not a vacuous pass.
        assert await registry.evict_stale(stale_after=_STALE_AFTER, keep=caller.node_id) == 1
        assert [r.node_id for r in await registry.load_all()] == [caller.node_id]

    async def test_evict_stale_returns_zero_when_only_the_caller_is_stale(
        self,
        node_registry_backend: NodeRegistryBackend,
    ) -> None:
        registry = node_registry_backend.make()
        caller = NodeIdentity.create('node-a')
        await registry.register(caller, capabilities=frozenset())

        await node_registry_backend.advance(_BEYOND_STALE)

        assert await registry.evict_stale(stale_after=_STALE_AFTER, keep=caller.node_id) == 0
        assert [r.node_id for r in await registry.load_all()] == [caller.node_id]

    async def test_evict_stale_is_idempotent_across_racing_callers(
        self,
        node_registry_backend: NodeRegistryBackend,
    ) -> None:
        first_caller = node_registry_backend.make()
        second_caller = node_registry_backend.make()
        keeper = NodeIdentity.create('node-keeper')
        await first_caller.register(NodeIdentity.create('node-a'), capabilities=frozenset())
        await first_caller.register(NodeIdentity.create('node-b'), capabilities=frozenset())

        await node_registry_backend.advance(_BEYOND_STALE)
        await first_caller.register(keeper, capabilities=frozenset())

        first = await first_caller.evict_stale(stale_after=_STALE_AFTER, keep=keeper.node_id)
        second = await second_caller.evict_stale(stale_after=_STALE_AFTER, keep=keeper.node_id)

        # Each row is removed exactly once no matter how many sweepers run.
        assert (first, second) == (2, 0)
        assert [r.node_id for r in await second_caller.load_all()] == [keeper.node_id]

    async def test_capabilities_round_trip(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')

        await registry.register(identity, capabilities=frozenset({'durability', 'projections'}))

        assert (await registry.load_all())[0].capabilities == frozenset({'durability', 'projections'})

    async def test_reregister_replaces_capabilities(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')
        await registry.register(identity, capabilities=frozenset({'durability'}))

        await registry.register(identity, capabilities=frozenset({'projections'}))

        assert (await registry.load_all())[0].capabilities == frozenset({'projections'})

    async def test_empty_capabilities_round_trip(self, node_registry_backend: NodeRegistryBackend) -> None:
        registry = node_registry_backend.make()
        identity = NodeIdentity.create('node-a')

        await registry.register(identity, capabilities=frozenset())

        assert (await registry.load_all())[0].capabilities == frozenset()
