from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Never

from waku._internal.transaction import Commit, TransactionDecision, run_committed

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.base import Endpoint

__all__ = ['AppScopeSource', 'dispatch_owned']


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


async def dispatch_owned(
    app_scope: AppScopeSource,
    ambient_container: AsyncContainer,
    envelope: MessageEnvelope[Any],
    endpoints: Sequence[Endpoint],
    /,
) -> None:
    """Dispatch a resolved endpoint set with per-endpoint durable ownership — the single authority.

    Outbox-backed destinations stage inside ONE committed, isolated transaction on the APP-scope owner
    (send-now: commits regardless of any ambient handler tx) and fire ``sent`` only post-commit;
    non-outbox-backed destinations take no owner and dispatch on ``ambient_container`` AFTER that commit,
    so a durable rollback (re-raised) yields no partial delivery. The APP-scope owner is materialized
    only when there IS outbox work. Both the direct-send bus and the dead-letter replay route through
    here (D-CRIT-1..4).
    """
    outbox_backed = [endpoint for endpoint in endpoints if endpoint.is_outbox_backed]
    passthrough = [endpoint for endpoint in endpoints if not endpoint.is_outbox_backed]
    if outbox_backed:
        await _own_and_emit_sent(app_scope.container, envelope, outbox_backed)
    for endpoint in passthrough:
        await endpoint.dispatch(envelope, ambient_container)


async def _own_and_emit_sent(
    container: AsyncContainer,
    envelope: MessageEnvelope[Any],
    endpoints: Sequence[Endpoint],
) -> None:
    async def stage(scope: AsyncContainer) -> TransactionDecision[None, Never]:
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, scope)
        return Commit(None)

    await run_committed(container, stage)
    for endpoint in endpoints:
        await endpoint.emit_sent(envelope)
