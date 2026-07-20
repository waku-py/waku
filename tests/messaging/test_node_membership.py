from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import anyio.lowlevel
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku import INodeRegistry, NodeIdentity, NodeRegistryConfig
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.extensions import AfterApplicationInit, OnContainerBuilt
from waku.messaging import MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging._internal.node_membership import NodeMembershipAgent, NodeMembershipLifecycleExtension
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import pytest

    from waku import DynamicModule, NodeId
    from waku.application import WakuApplication

# Fast enough that a silent peer goes stale within a poll loop, still honouring the 3x anti-flap floor.
_FAST = NodeRegistryConfig(
    heartbeat_interval=timedelta(milliseconds=10),
    stale_after=timedelta(milliseconds=30),
    evict_interval=timedelta(milliseconds=10),
)

_MEMBERSHIP_LOGGER = 'waku.messaging._internal.node_membership'
_POLLING_LOGGER = 'waku.messaging._internal.polling_agent'


def _durable_app_modules(*, registry_config: NodeRegistryConfig | None = None) -> list[DynamicModule]:
    backend = (
        MemoryBackend.register()
        if registry_config is None
        else MemoryBackend.register(node_registry_config=registry_config)
    )
    return [MessagingModule.register(MessagingConfig(outbox=OutboxConfig())), backend]


async def _members(app: WakuApplication) -> list[NodeId]:
    async with app.container() as scope:
        registry = await scope.get(INodeRegistry)
        return [registration.node_id for registration in await registry.load_all()]


async def _wait_for_members(app: WakuApplication, predicate: Callable[[Sequence[NodeId]], bool]) -> None:
    with anyio.fail_after(5):
        while not predicate(await _members(app)):
            await anyio.lowlevel.checkpoint()


class _MembershipProbe(OnContainerBuilt, AfterApplicationInit):
    """Reads membership at the phase preceding every claimer's start, and again at that start."""

    def __init__(self) -> None:
        self.before_claimers: list[NodeId] = []
        self.at_claimer_start: list[NodeId] = []

    @override
    async def on_container_built(self, app: WakuApplication) -> None:
        self.before_claimers = await _members(app)

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        self.at_claimer_start = await _members(app)


async def test_node_registered_before_first_claim() -> None:
    # The outbox relay and the maintenance agent — the claimers of this app's durable rows — are started
    # by their lifecycle extensions at after_app_init. Membership is already committed one phase earlier,
    # so no claim can ever be attributed to a node the registry has not heard of.
    probe = _MembershipProbe()

    async with create_test_app(imports=_durable_app_modules(), extensions=[probe]) as app:
        identity = await app.container.get(NodeIdentity)

    assert probe.before_claimers == [identity.node_id]
    assert probe.at_claimer_start == [identity.node_id]


async def test_freshly_registered_node_does_not_heartbeat_in_its_first_cycle() -> None:
    # Registration stamped both timestamps; an immediate heartbeat would only rewrite what it just
    # wrote, and would have every pod of a rolling deploy hit the registry the instant it boots.
    async with create_test_app(imports=_durable_app_modules()) as app:
        await anyio.lowlevel.checkpoint()
        async with app.container() as scope:
            registered = await (await scope.get(INodeRegistry)).load_all()

    assert len(registered) == 1
    assert registered[0].last_heartbeat == registered[0].started_at


async def test_membership_stays_empty_without_durability() -> None:
    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
    ) as app:
        assert await _members(app) == []


async def test_evicted_node_reregisters_on_next_tick(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_MEMBERSHIP_LOGGER):
        async with create_test_app(imports=_durable_app_modules(registry_config=_FAST)) as app:
            identity = await app.container.get(NodeIdentity)
            async with app.container() as scope:
                await (await scope.get(INodeRegistry)).deregister(identity.node_id)
                await (await scope.get(IUnitOfWork)).commit()

            await _wait_for_members(app, lambda members: identity.node_id in members)

    assert any(identity.node_id in record.getMessage() for record in caplog.records)


async def test_clean_shutdown_deregisters_immediately() -> None:
    async with create_test_app(imports=_durable_app_modules()) as app:
        identity = await app.container.get(NodeIdentity)
        assert await _members(app) == [identity.node_id]

        await app.close()

        assert await _members(app) == []


async def test_cancelled_shutdown_still_deregisters_this_node() -> None:
    # The handover is immediate only because the deregister is shielded AND unconditional: a shutdown
    # cancelled while the agent is stopping must still hand this node's rows back, or they stay owned
    # by a process that no longer exists until the stale timeout expires.
    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
    ) as app:
        extension = NodeMembershipLifecycleExtension()
        await extension.on_container_built(app)
        await extension.after_app_init(app)
        identity = await app.container.get(NodeIdentity)
        assert await _members(app) == [identity.node_id]

        with anyio.CancelScope() as scope:
            scope.cancel()
            await extension.on_app_shutdown(app)

        assert await _members(app) == []


class _CountingNodeRegistry(InMemoryNodeRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.heartbeats = 0

    @override
    async def heartbeat(self, node_id: NodeId) -> bool:
        self.heartbeats += 1
        return await super().heartbeat(node_id)


class _FlakyUnitOfWork(IUnitOfWork):
    # Failing the commit AND the required rollback is the shape that yields a bare RollbackFailedError,
    # the fatal signal a PollingAgent treats as terminal.
    def __init__(self, *, failures: int) -> None:
        self._remaining = failures

    @override
    async def commit(self) -> None:
        if self._remaining:
            msg = 'membership commit failed'
            raise RuntimeError(msg)

    @override
    async def rollback(self) -> None:
        if self._remaining:
            self._remaining -= 1
            msg = 'membership rollback failed'
            raise RuntimeError(msg)


class _MembershipDeps(Provider):
    scope = Scope.REQUEST

    def __init__(self, *, registry: INodeRegistry, uow: IUnitOfWork) -> None:
        super().__init__()
        self._registry = registry
        self._uow = uow

    @provide
    def registry(self) -> INodeRegistry:
        return self._registry

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


async def test_heartbeat_loop_survives_a_fatal_membership_transaction(caplog: pytest.LogCaptureFixture) -> None:
    # A dead heartbeat loop is worse than a stalled maintenance loop: the process keeps running and
    # keeps owning durable rows while last_heartbeat freezes, so a peer reclaims a healthy node's
    # in-flight work. A failed membership transaction is therefore retried, never escalated.
    registry = _CountingNodeRegistry()
    identity = NodeIdentity.create('flaky-store-node')

    with caplog.at_level(logging.ERROR, logger=_POLLING_LOGGER):
        async with make_async_container(
            _MembershipDeps(registry=registry, uow=_FlakyUnitOfWork(failures=1)),
        ) as container:
            await registry.register(identity, capabilities=frozenset())
            agent = NodeMembershipAgent(container=container, identity=identity, config=_FAST)
            await agent.start()
            try:
                await wait_until(lambda: registry.heartbeats >= 3)
            finally:
                await agent.stop()

    assert 'NodeMembershipAgent tick failed with an unrecoverable transaction error' in caplog.text


async def test_running_agent_evicts_a_silent_peer_but_never_itself() -> None:
    peer = NodeIdentity.create('silent-peer')

    async with create_test_app(imports=_durable_app_modules(registry_config=_FAST)) as app:
        identity = await app.container.get(NodeIdentity)
        async with app.container() as scope:
            await (await scope.get(INodeRegistry)).register(peer, capabilities=frozenset())
            await (await scope.get(IUnitOfWork)).commit()

        await _wait_for_members(app, lambda members: peer.node_id not in members)

        assert await _members(app) == [identity.node_id]
