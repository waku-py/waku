from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Never, assert_never
from uuid import uuid4

import anyio
from typing_extensions import override

from waku._internal.transaction import (
    Aborted,
    AfterCommitError,
    Commit,
    Committed,
    RolledBack,
    TransactionDecision,
    TransactionExecutionError,
    execute_in_uow_scope,
    extract_transaction_execution_error,
    run_committed,
)
from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging._internal.dispatcher import MessageDispatcher  # noqa: TC001
from waku.messaging._internal.identity import MessageTypeRegistry  # noqa: TC001
from waku.messaging._internal.ownership import AppScopeSource, dispatch_owned
from waku.messaging.context import message_context_scope
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, ReplayClaimId
from waku.messaging.handler_map import HandlerMap  # noqa: TC001
from waku.messaging.inbox.destination import handler_map_by_destination
from waku.messaging.inbox.identifiers import HandlerDestination
from waku.messaging.router import MessageRouter  # noqa: TC001
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec  # noqa: TC001

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from anyio.abc import TaskGroup

    from waku._internal.clock import Now
    from waku._internal.node import NodeId
    from waku.messaging.config import DeadLetterConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.dead_letter import DeadLetterEntry

logger = logging.getLogger(__name__)


class _ReplayRenewalError(TransactionExecutionError):
    """Transaction-fatal renewal loss that cannot prove dispatch success."""

    __slots__ = ()


class IReplayExecution(ABC):
    @abstractmethod
    async def dispatch(self, entry: DeadLetterEntry) -> None: ...


class ReplayExecution(IReplayExecution):
    """Dispatch-only dead-letter reconstruction and destination selection."""

    __slots__ = (
        '_app_scope',
        '_codec',
        '_container',
        '_dispatcher',
        '_handler_by_fqn',
        '_router',
        '_type_registry',
    )

    def __init__(
        self,
        *,
        container: AsyncContainer,
        codec: PayloadCodec,
        type_registry: MessageTypeRegistry,
        router: MessageRouter,
        dispatcher: MessageDispatcher,
        handler_map: HandlerMap,
        app_scope: AppScopeSource,
    ) -> None:
        self._container = container
        self._codec = codec
        self._type_registry = type_registry
        self._router = router
        self._dispatcher = dispatcher
        self._app_scope = app_scope
        self._handler_by_fqn: dict[HandlerDestination, HandlerType] = handler_map_by_destination(handler_map)

    @override
    async def dispatch(self, entry: DeadLetterEntry) -> None:
        envelope = rebuild_envelope(
            entry.payload,
            wire_metadata_from_entry(entry),
            self._codec,
            self._type_registry,
        )
        if entry.destination_kind is DeadLetterDestinationKind.HANDLER:
            await self._dispatch_to_handler(entry, envelope)
            return
        endpoint = self._router.endpoint_for(entry.destination)
        if endpoint is None:
            msg = f'no endpoint registered for destination {entry.destination!r}'
            raise RuntimeError(msg)
        # Same ownership law as the direct-send bus: an outbox-backed target restages inside ONE committed,
        # isolated APP-scope transaction (so the row survives teardown, ``sent`` fires post-commit); a
        # non-outbox-backed target dispatches on the ambient reprocess scope and takes no owner.
        await dispatch_owned(self._app_scope, self._container, envelope, [endpoint])

    async def _dispatch_to_handler(self, entry: DeadLetterEntry, envelope: MessageEnvelope[Any]) -> None:
        handler_type = self._handler_by_fqn.get(HandlerDestination(entry.destination))
        if handler_type is None:
            msg = f'no registered handler for destination {entry.destination!r}'
            raise RuntimeError(msg)

        dispatch_completed = False
        try:
            async with self._app_scope.fresh_scope() as scope:
                with message_context_scope(envelope):
                    await self._dispatcher.dispatch_to_handler(scope, envelope, handler_type)
                    dispatch_completed = True
        except BaseException as error:
            if dispatch_completed:
                raise AfterCommitError(error) from error
            raise


@dataclass(frozen=True, slots=True)
class ReplayClaim:
    """One successful claim: the leased row plus the token that fences its finalization.

    The token is non-optional here precisely where it is load-bearing — ``DeadLetterEntry`` mirrors
    the row and so carries ``replay_claim_id`` as ``| None``.
    """

    entry: DeadLetterEntry
    claim_id: ReplayClaimId


class ReplayClaimOwner:
    """Shared mechanism for public and maintenance replay owners over short strict transactions.

    Holds the node's owner token for attribution and claiming; every successful claim additionally
    mints a fresh ``ReplayClaimId``, and that token — never the owner — fences renewal and
    finalization. Two claimants in one process share ``owner_id``, so an owner-keyed fence could not
    tell a lapsed dispatch from its successor.
    """

    __slots__ = ('_config', '_container', '_now', '_owner_id')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        config: DeadLetterConfig,
        node_id: NodeId,
        now: Now,
    ) -> None:
        self._container = container
        self._config = config
        self._now = now
        self._owner_id = node_id

    @property
    def owner_id(self) -> NodeId:
        return self._owner_id

    async def claim_replayable(self) -> ReplayClaim | None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)
        claim_id = ReplayClaimId(uuid4())

        async def claim(scope: AsyncContainer) -> TransactionDecision[DeadLetterEntry | None, Never]:
            store = await scope.get(IDeadLetterStore)
            entry = await store.claim_replayable(
                self._config.max_replay_count,
                owner_id=self._owner_id,
                claim_id=claim_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(entry)

        entry = await run_committed(self._container, claim)
        return ReplayClaim(entry, claim_id) if entry is not None else None

    async def claim_replay(self, entry_id: UUID) -> ReplayClaim | None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)
        claim_id = ReplayClaimId(uuid4())

        async def claim(scope: AsyncContainer) -> TransactionDecision[DeadLetterEntry | None, Never]:
            store = await scope.get(IDeadLetterStore)
            entry = await store.claim_replay(
                entry_id,
                owner_id=self._owner_id,
                claim_id=claim_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(entry)

        entry = await run_committed(self._container, claim)
        return ReplayClaim(entry, claim_id) if entry is not None else None

    async def replay_claimed(self, claim: ReplayClaim, execution: IReplayExecution) -> bool:
        try:
            await self._dispatch_with_renewal(claim, execution)
        except BaseException as error:
            fatal = extract_transaction_execution_error(error)
            if fatal is not None:
                if isinstance(fatal, _ReplayRenewalError):
                    raise
                if isinstance(fatal, AfterCommitError):
                    with anyio.CancelScope(shield=True):
                        await self._finalize_replayed(claim, primary_error=fatal)
                raise
            if isinstance(error, Exception):
                await self._finalize_failed(claim, error)
                logger.warning('Replay failed for dead letter %s: %s', claim.entry.id, error)
                return False
            await self._finalize_cancelled(claim, error)
            raise

        await self._finalize_replayed(claim)
        return True

    async def _dispatch_with_renewal(self, claim: ReplayClaim, execution: IReplayExecution) -> None:
        dispatch_finished = anyio.Event()
        dispatch_error: BaseException | None = None
        renewal_error: BaseException | None = None

        async def dispatch() -> None:
            nonlocal dispatch_error
            try:
                await execution.dispatch(claim.entry)
            except BaseException as error:  # noqa: BLE001 -- cancellation is data until owner cleanup completes
                dispatch_error = error
            finally:
                dispatch_finished.set()

        async def renew(task_group: TaskGroup) -> None:
            nonlocal renewal_error
            while True:
                with anyio.move_on_after(self._config.replay_lease.renew_interval_seconds) as wait_scope:
                    await dispatch_finished.wait()
                if not wait_scope.cancel_called:
                    return
                try:
                    await self._renew(claim)
                except BaseException as error:  # noqa: BLE001 -- cancellation/fatal renewal must stop dispatch
                    renewal_error = _renewal_error(error)
                    task_group.cancel_scope.cancel()
                    return

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(dispatch)
            task_group.start_soon(renew, task_group)

        if renewal_error is not None:
            raise renewal_error
        if dispatch_error is not None:
            raise dispatch_error

    async def _renew(self, claim: ReplayClaim) -> None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)

        async def renew(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            renewed = await store.renew_replay_claim(
                claim.entry.id,
                claim_id=claim.claim_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(renewed)

        if not await run_committed(self._container, renew):
            raise _lost_claim(claim.entry.id)

    async def _finalize_replayed(
        self,
        claim: ReplayClaim,
        *,
        primary_error: BaseException | None = None,
    ) -> None:
        now = self._now()

        async def finalize(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            marked = await store.mark_replayed(claim.entry.id, claim_id=claim.claim_id, now=now)
            return Commit(marked)

        try:
            result = await execute_in_uow_scope(self._container, finalize)
        except TransactionExecutionError as error:
            if primary_error is None:
                raise
            raise type(error)(error.error, primary_error) from error
        if isinstance(result, Committed):
            if result.value:
                return
            raise _lost_claim(claim.entry.id, primary_error=primary_error)
        if isinstance(result, Aborted):
            raise AfterCommitError(result.error, primary_error) from result.error
        if isinstance(result, RolledBack):
            assert_never(result.value)
        assert_never(result)

    async def _finalize_failed(self, claim: ReplayClaim, error: Exception) -> None:
        rendered_error = ''.join(traceback.format_exception(error))
        await self._mark_failed(claim, rendered_error, primary_error=error)

    async def _mark_failed(
        self,
        claim: ReplayClaim,
        rendered_error: str,
        *,
        primary_error: BaseException,
    ) -> None:
        now = self._now()

        async def finalize(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            marked = await store.mark_replay_failed(
                claim.entry.id,
                rendered_error,
                claim_id=claim.claim_id,
                now=now,
            )
            return Commit(marked)

        if not await run_committed(self._container, finalize):
            raise _lost_claim(claim.entry.id, primary_error=primary_error)

    async def _finalize_cancelled(self, claim: ReplayClaim, error: BaseException) -> None:
        rendered_error = ''.join(traceback.format_exception(error))
        try:
            with anyio.CancelScope(shield=True):
                await self._mark_failed(claim, rendered_error, primary_error=error)
        except BaseException as finalization_error:  # noqa: BLE001 -- cancellation remains the primary signal
            error.__cause__ = finalization_error

    def _lease_expires_at(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._config.replay_lease.ttl_seconds)


def _lost_claim(entry_id: UUID, *, primary_error: BaseException | None = None) -> TransactionExecutionError:
    error = RuntimeError(f'Replay claim ownership was lost for dead letter {entry_id}')
    return AfterCommitError(error, primary_error)


def _renewal_error(error: BaseException) -> BaseException:
    fatal = extract_transaction_execution_error(error)
    if fatal is None:
        return error
    return _ReplayRenewalError(fatal.error, fatal.primary_error)
