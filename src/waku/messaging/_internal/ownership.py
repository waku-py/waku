from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Never

from waku._internal.transaction import Commit, TransactionDecision, run_committed

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.base import Endpoint

__all__ = ['AppScopeSource', 'own_and_emit_sent']


class AppScopeSource:
    """Captured APP-scope container for owners that must not touch the ambient request scope.

    A REQUEST-scoped ``container()`` descends to ACTION and shares REQUEST-scoped providers with the
    ambient request; owning an isolated transaction (or running a handler pipeline in its own tx)
    needs the APP container, whose ``container()`` opens a fresh sibling REQUEST scope
    (the drainer/relay/executor pattern).
    """

    __slots__ = ('_container',)

    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    @property
    def container(self) -> AsyncContainer:
        return self._container

    @asynccontextmanager
    async def fresh_scope(self) -> AsyncGenerator[AsyncContainer]:
        async with self._container() as scope:
            yield scope


async def own_and_emit_sent(
    container: AsyncContainer,
    envelope: MessageEnvelope[Any],
    endpoints: Sequence[Endpoint],
    /,
) -> None:
    """Stage outbox-backed dispatch in ONE committed, isolated transaction, then fire ``sent`` post-commit.

    The single authority for the CRIT direct-ownership law (D-CRIT-1..4).
    """

    async def stage(scope: AsyncContainer) -> TransactionDecision[None, Never]:
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, scope)
        return Commit(None)

    await run_committed(container, stage)
    for endpoint in endpoints:
        await endpoint.emit_sent(envelope)
