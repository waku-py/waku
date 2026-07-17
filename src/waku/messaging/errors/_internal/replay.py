from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Never, assert_never
from uuid import UUID, uuid4

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
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind
from waku.messaging.handler_map import HandlerMap  # noqa: TC001
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.router import MessageRouter  # noqa: TC001
from waku.messaging.transport._internal.wire import rebuild_envelope, wire_metadata_from_entry
from waku.serialization.codec import PayloadCodec  # noqa: TC001

if TYPE_CHECKING:
    from datetime import datetime

    from anyio.abc import TaskGroup

    from waku._internal.clock import Now
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
        self._handler_by_fqn: dict[str, HandlerType] = {
            handler_destination(handler_type): handler_type for handler_type in handler_map.handler_types()
        }

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
        handler_type = self._handler_by_fqn.get(entry.destination)
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


class ReplayClaimOwner:
    """Shared mechanism for public and maintenance replay owners over short strict transactions."""

    __slots__ = ('_config', '_container', '_now', '_owner_id')

    def __init__(self, *, container: AsyncContainer, config: DeadLetterConfig, now: Now) -> None:
        self._container = container
        self._config = config
        self._now = now
        self._owner_id = str(uuid4())

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def claim_replayable(self) -> DeadLetterEntry | None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)

        async def claim(scope: AsyncContainer) -> TransactionDecision[DeadLetterEntry | None, Never]:
            store = await scope.get(IDeadLetterStore)
            entry = await store.claim_replayable(
                self._config.max_replay_count,
                owner_id=self._owner_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(entry)

        return await run_committed(self._container, claim)

    async def claim_replay(self, entry_id: UUID) -> DeadLetterEntry | None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)

        async def claim(scope: AsyncContainer) -> TransactionDecision[DeadLetterEntry | None, Never]:
            store = await scope.get(IDeadLetterStore)
            entry = await store.claim_replay(
                entry_id,
                owner_id=self._owner_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(entry)

        return await run_committed(self._container, claim)

    async def replay_claimed(self, entry: DeadLetterEntry, execution: IReplayExecution) -> bool:
        try:
            await self._dispatch_with_renewal(entry, execution)
        except BaseException as error:
            fatal = extract_transaction_execution_error(error)
            if fatal is not None:
                if isinstance(fatal, _ReplayRenewalError):
                    raise
                if isinstance(fatal, AfterCommitError):
                    with anyio.CancelScope(shield=True):
                        await self._finalize_replayed(entry.id, primary_error=fatal)
                raise
            if isinstance(error, Exception):
                await self._finalize_failed(entry.id, error)
                logger.warning('Replay failed for dead letter %s: %s', entry.id, error)
                return False
            await self._finalize_cancelled(entry.id, error)
            raise

        await self._finalize_replayed(entry.id)
        return True

    async def _dispatch_with_renewal(self, entry: DeadLetterEntry, execution: IReplayExecution) -> None:
        dispatch_finished = anyio.Event()
        dispatch_error: BaseException | None = None
        renewal_error: BaseException | None = None

        async def dispatch() -> None:
            nonlocal dispatch_error
            try:
                await execution.dispatch(entry)
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
                    await self._renew(entry.id)
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

    async def _renew(self, entry_id: UUID) -> None:
        now = self._now()
        lease_expires_at = self._lease_expires_at(now)

        async def renew(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            renewed = await store.renew_replay_claim(
                entry_id,
                owner_id=self._owner_id,
                now=now,
                lease_expires_at=lease_expires_at,
            )
            return Commit(renewed)

        if not await run_committed(self._container, renew):
            raise _lost_claim(entry_id)

    async def _finalize_replayed(
        self,
        entry_id: UUID,
        *,
        primary_error: BaseException | None = None,
    ) -> None:
        now = self._now()

        async def finalize(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            marked = await store.mark_replayed(entry_id, owner_id=self._owner_id, now=now)
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
            raise _lost_claim(entry_id, primary_error=primary_error)
        if isinstance(result, Aborted):
            raise AfterCommitError(result.error, primary_error) from result.error
        if isinstance(result, RolledBack):
            assert_never(result.value)
        assert_never(result)

    async def _finalize_failed(self, entry_id: UUID, error: Exception) -> None:
        rendered_error = ''.join(traceback.format_exception(error))
        await self._mark_failed(entry_id, rendered_error, primary_error=error)

    async def _mark_failed(
        self,
        entry_id: UUID,
        rendered_error: str,
        *,
        primary_error: BaseException,
    ) -> None:
        now = self._now()

        async def finalize(scope: AsyncContainer) -> TransactionDecision[bool, Never]:
            store = await scope.get(IDeadLetterStore)
            marked = await store.mark_replay_failed(
                entry_id,
                rendered_error,
                owner_id=self._owner_id,
                now=now,
            )
            return Commit(marked)

        if not await run_committed(self._container, finalize):
            raise _lost_claim(entry_id, primary_error=primary_error)

    async def _finalize_cancelled(self, entry_id: UUID, error: BaseException) -> None:
        rendered_error = ''.join(traceback.format_exception(error))
        try:
            with anyio.CancelScope(shield=True):
                await self._mark_failed(entry_id, rendered_error, primary_error=error)
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
